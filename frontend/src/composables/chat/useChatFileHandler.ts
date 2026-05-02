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

// ─── On-screen diagnostic overlay (no DevTools required) ───────────────────
// Activated when the URL contains ?chatFileDebug=1 OR when
// localStorage.chatFileDebug === '1'. Shows the last 12 [chat-file] events
// in a fixed bottom-left box so users on installed PWAs (where DevTools is
// not accessible) can still report what's happening.
function isDiagEnabled(): boolean {
    try {
        if (typeof window === 'undefined') return false
        const sp = new URLSearchParams(window.location.search)
        if (sp.get('chatFileDebug') === '1') {
            try { localStorage.setItem('chatFileDebug', '1') } catch { /* noop */ }
            return true
        }
        return localStorage.getItem('chatFileDebug') === '1'
    } catch { return false }
}

let diagBox: HTMLDivElement | null = null
const diagLines: string[] = []

function ensureDiagBox(): HTMLDivElement | null {
    if (!isDiagEnabled() || typeof document === 'undefined') return null
    if (diagBox && document.body.contains(diagBox)) return diagBox
    const el = document.createElement('div')
    el.id = 'chat-file-debug-box'
    el.style.cssText = [
        'position:fixed', 'left:8px', 'bottom:8px', 'z-index:2147483647',
        'max-width:92vw', 'max-height:42vh', 'overflow:auto',
        'background:rgba(0,0,0,0.82)', 'color:#0f0', 'font:11px/1.35 monospace',
        'padding:8px 10px', 'border-radius:8px', 'pointer-events:auto',
        'direction:ltr', 'text-align:left', 'white-space:pre-wrap',
        'box-shadow:0 2px 12px rgba(0,0,0,0.4)',
    ].join(';')
    el.addEventListener('click', () => { try { el.remove() } catch { /* noop */ } })
    document.body.appendChild(el)
    diagBox = el
    return el
}

function diagLog(...parts: unknown[]) {
    const stamp = new Date().toLocaleTimeString('en-GB', { hour12: false })
    const line = '[' + stamp + '] ' + parts.map((p) => {
        if (p instanceof Error) return p.name + ': ' + p.message
        if (typeof p === 'object' && p !== null) {
            try { return JSON.stringify(p) } catch { return String(p) }
        }
        return String(p)
    }).join(' ')
    diagLines.push(line)
    while (diagLines.length > 12) diagLines.shift()
    const box = ensureDiagBox()
    if (box) box.textContent = diagLines.join('\n')
}

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

function shareBlobSync(entry: CachedFileEntry, fallbackName: string): Promise<boolean> | false {
    // SYNCHRONOUS-FRIENDLY share invocation. Returns either a Promise that
    // resolves to true/false, OR the literal value `false` when share isn't
    // available at all on this device. Crucially, navigator.share(...) is
    // invoked synchronously in the call-site task so the browser's transient
    // user-activation token is not consumed by an awaited operation first.
    const navAny = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean
        share?: (data: ShareData) => Promise<void>
    }
    diagLog('share() invoked sync; ua-activation:', navigator.userActivation?.isActive, 'mime:', entry.mimeType, 'size:', entry.blob?.size)
    if (typeof navAny.share !== 'function') {
        console.info('[chat-file] share API unavailable (no navigator.share)')
        diagLog('navigator.share MISSING -> fallback')
        return false
    }
    let file: File
    try {
        file = new File(
            [entry.blob],
            entry.fileName || fallbackName || 'file',
            { type: entry.mimeType || entry.blob.type || 'application/octet-stream' },
        )
    } catch (err) {
        console.warn('[chat-file] File() construction failed', err)
        diagLog('File() construct FAILED:', err as Error)
        return false
    }
    const shareData: ShareData = { files: [file] }
    if (typeof navAny.canShare === 'function') {
        try {
            const ok = navAny.canShare(shareData)
            diagLog('canShare ->', ok, 'type:', file.type)
            if (!ok) console.info('[chat-file] canShare returned false; trying share() anyway', file.type)
        } catch (err) { console.warn('[chat-file] canShare threw', err); diagLog('canShare threw:', err as Error) }
    }
    let promise: Promise<void>
    try {
        // Synchronous invocation — must NOT be `await`ed before this line.
        promise = navAny.share(shareData)
    } catch (err) {
        // Some browsers throw synchronously (e.g. NotAllowedError when
        // activation is missing) instead of rejecting.
        const name = (err as DOMException)?.name
        if (name === 'AbortError') { diagLog('share aborted (sync) -> ok'); return Promise.resolve(true) }
        console.warn('[chat-file] share threw sync', name, err)
        diagLog('share THREW sync:', name || 'Error', '-', (err as Error)?.message || String(err))
        return false
    }
    diagLog('share() returned promise; awaiting...')
    return promise
        .then(() => { diagLog('share RESOLVED ok'); return true })
        .catch((err: unknown) => {
            const name = (err as DOMException)?.name
            if (name === 'AbortError') { diagLog('share aborted (async) -> ok'); return true }
            console.warn('[chat-file] share rejected', name, err)
            diagLog('share REJECTED:', name || 'Error', '-', (err as Error)?.message || String(err))
            return false
        })
}

async function shareBlob(entry: CachedFileEntry, fallbackName: string): Promise<boolean> {
    const result = shareBlobSync(entry, fallbackName)
    if (result === false) return false
    return result
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


function fallbackSaveFromCache(entry: CachedFileEntry, displayName: string) {
    // When navigator.share is unavailable or rejected for a binary file,
    // anchor-download from the *cached blob* (no network fetch) is the only
    // way to deliver the file to the user. Chrome will show its save UI but
    // the bytes come from IndexedDB, not the server. Showing an alert with
    // no actionable button leaves the user stranded with no way to access
    // the file, so we always fall back to a real save action here.
    diagLog('fallbackSaveFromCache -> anchor download (offline)')
    triggerAnchorDownload(entry.blob, displayName)
}

async function presentCachedFile(entry: CachedFileEntry, fileName: string, mode: 'open' | 'share' | 'download' = 'open'): Promise<void> {
    const displayName = entry.fileName || fileName || 'file'
    const mimeType = entry.mimeType || entry.blob.type || ''
    diagLog('present mode=' + mode, 'name=' + displayName, 'mime=' + mimeType, 'inline=' + isInlineViewable(mimeType, displayName))

    if (mode === 'download') {
        // Explicit save-to-disk only. This is the ONLY path that ever creates
        // an anchor download. Tap-to-open and Share never reach it.
        triggerAnchorDownload(entry.blob, displayName)
        return
    }

    if (mode === 'share') {
        // Share button: invoke navigator.share SYNCHRONOUSLY before any await
        // so the user-activation token is preserved on Android Chrome HTTPS.
        const result = shareBlobSync(entry, displayName)
        if (result === false) {
            // No share API at all (insecure context, desktop, etc.) — for
            // renderable types open inline; for binary types save from cache.
            if (isInlineViewable(mimeType, displayName)) {
                openBlobInTab(entry.blob, displayName)
            } else {
                fallbackSaveFromCache(entry, displayName)
            }
            return
        }
        const shared = await result
        if (shared) return
        // share() rejected (NotAllowedError / SecurityError). Last-ditch
        // fallback: only open inline if renderable; otherwise save from cache.
        if (isInlineViewable(mimeType, displayName)) {
            openBlobInTab(entry.blob, displayName)
        } else {
            fallbackSaveFromCache(entry, displayName)
        }
        return
    }

    // mode === 'open' (tap on file body)
    // Renderable formats (PDF, images, video, audio): open inline.
    if (isInlineViewable(mimeType, displayName)) {
        if (openBlobInTab(entry.blob, displayName)) return
        // Popup-blocked → try synchronous share.
        const result = shareBlobSync(entry, displayName)
        if (result === false) return
        await result
        return
    }
    // Binary formats (xlsx, heic, docx, txt, zip, ...): SYNCHRONOUSLY invoke
    // share so activation is preserved. If share is unavailable or rejects,
    // fall back to a real save-from-cache action so the user can always
    // access the file. The save uses the cached blob (zero network).
    const result = shareBlobSync(entry, displayName)
    if (result === false) {
        fallbackSaveFromCache(entry, displayName)
        return
    }
    const shared = await result
    if (!shared) {
        fallbackSaveFromCache(entry, displayName)
    }
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
 * Pre-warm the synchronous in-memory cache for a file by promoting its
 * IndexedDB entry. Call this when a document bubble is mounted/visible so
 * that the user's first tap can call navigator.share() synchronously
 * without first awaiting an IDB read (which would consume transient user
 * activation on Android Chrome and cause share() to throw NotAllowedError).
 */
export async function prewarmFileCache(fileId: string): Promise<void> {
    if (!fileId) return
    if (memoryCache.has(fileId)) return
    try {
        const entry = await fileStore.getItem(fileId)
        if (isCachedFileEntry(entry)) {
            memoryCache.set(fileId, entry)
        }
    } catch { /* noop */ }
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
