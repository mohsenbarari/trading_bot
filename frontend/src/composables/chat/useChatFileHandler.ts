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

// In-memory mirror of recently fetched files, keyed by file_id. We need this
// because navigator.share() on Android Chrome requires *transient user
// activation* — awaiting an async IndexedDB read inside the click handler
// consumes the activation token, after which share() throws NotAllowedError.
// By keeping a synchronous Map mirror, subsequent taps on a previously-fetched
// file resolve immediately without breaking the user-activation window.
const memoryCache = new Map<string, CachedFileEntry>()

function isCachedFileEntry(value: unknown): value is CachedFileEntry {
    return Boolean(
        value
        && typeof value === 'object'
        && (value as CachedFileEntry).blob instanceof Blob,
    )
}

function readMemoryEntry(fileId: string): CachedFileEntry | null {
    return memoryCache.get(fileId) || null
}

async function readCachedEntry(fileId: string): Promise<CachedFileEntry | null> {
    const inMemory = memoryCache.get(fileId)
    if (inMemory) return inMemory
    try {
        const entry = await fileStore.getItem(fileId)
        if (isCachedFileEntry(entry)) {
            memoryCache.set(fileId, entry)
            return entry
        }
        return null
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

// Strict whitelist: only mime types Chrome/Safari actually render inline via
// blob: URL without diverting to the OS download manager. Text formats
// (text/plain, json, csv, xml, html...) are INTENTIONALLY EXCLUDED — Chrome
// for Android downloads blob: URLs of those types instead of opening them,
// triggering a 'Download again?' prompt on every tap. HEIC/HEIF are also
// excluded because Chrome cannot decode them inline.
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
])

const INLINE_VIEWABLE_EXTS = new Set([
    'pdf',
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'avif', 'bmp',
    'mp4', 'webm', 'ogg',
    'mp3', 'wav', 'm4a', 'aac',
])

function isInlineViewable(mimeType: string, fileName: string): boolean {
    const mt = (mimeType || '').toLowerCase()
    if (mt.includes('heic') || mt.includes('heif')) return false
    if (mt && INLINE_VIEWABLE_EXACT.has(mt)) return true
    const ext = (fileName.split('.').pop() || '').toLowerCase()
    return INLINE_VIEWABLE_EXTS.has(ext)
}

async function shareBlob(entry: CachedFileEntry, fallbackName: string): Promise<boolean> {
    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }
    if (typeof navAny.share !== 'function') return false
    const file = new File(
        [entry.blob],
        entry.fileName || fallbackName || 'file',
        { type: entry.mimeType || entry.blob.type || 'application/octet-stream' },
    )
    const shareData: ShareData = { files: [file] }
    // Note: we intentionally skip the canShare() gate and let navigator.share
    // throw if it really cannot accept this file. Some Android Chrome versions
    // return canShare({files}) === false even though share() works fine, and
    // we'd otherwise wrongly fall back to an anchor download.
    try {
        await navAny.share(shareData)
        return true
    } catch (err) {
        // AbortError = user dismissed the share sheet; treat as success so we
        // don't trigger any further fallback (which would re-download).
        const name = (err as DOMException)?.name
        if (name === 'AbortError') return true
        console.warn('[useChatFileHandler] share failed', err)
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
        // Explicit save-to-disk only. This is the ONLY path that ever creates
        // an anchor download. Tap-to-open and Share never reach it.
        triggerAnchorDownload(entry.blob, displayName)
        return
    }

    if (mode === 'share') {
        // Share button = native share sheet first. If unsupported (insecure
        // context / desktop / older browser) fall back to opening the blob in
        // a new tab so the user always sees SOMETHING happen. We never use
        // anchor download here because that would prompt "Download again?"
        // on every tap.
        const shared = await shareBlob(entry, displayName)
        if (shared) return
        openBlobInTab(entry.blob, displayName)
        return
    }

    // mode === 'open' (tap on file body)
    // For browser-renderable formats (PDF, images, video, audio): open inline
    // via window.open(blob:) — Chrome and Safari render these natively.
    if (isInlineViewable(mimeType, displayName)) {
        if (openBlobInTab(entry.blob, displayName)) return
        await shareBlob(entry, displayName)
        return
    }
    // For binary formats (txt, xlsx, docx, heic, zip, ...): try the OS share
    // sheet first (closest web equivalent to Android's "Open With..." picker
    // on HTTPS), and fall back to window.open(blob:) when share is
    // unavailable so the user can still get to the file. Anchor download is
    // never used here.
    const shared = await shareBlob(entry, displayName)
    if (shared) return
    openBlobInTab(entry.blob, displayName)
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
    memoryCache.set(fileId, entry)
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

    // SYNCHRONOUS fast-path: if the file is already in memory, call
    // presentCachedFile WITHOUT awaiting an IDB read first. This preserves the
    // browser's transient user-activation token so navigator.share() succeeds
    // for non-viewable formats on Android Chrome HTTPS.
    const memEntry = readMemoryEntry(fileId)
    if (memEntry) {
        // Don't await before presentCachedFile — share() must be called inside
        // the user-activation window started by the click event.
        void presentCachedFile(memEntry, memEntry.fileName || fileName, 'open')
        return
    }

    // Not yet in memory: try IDB (still async, but the very first tap on a
    // fresh page load may consume the activation; that's acceptable because
    // window.open / share will work on the first interaction in most cases
    // and subsequent taps hit the synchronous memory cache).
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

    // Synchronous fast-path to preserve transient user activation for share().
    const memEntry = readMemoryEntry(fileId)
    if (memEntry) {
        void presentCachedFile(memEntry, memEntry.fileName || fileName, 'share')
        return true
    }

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
        memoryCache.clear()
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
