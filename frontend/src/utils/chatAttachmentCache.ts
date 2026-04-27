const DB_NAME = 'chat_image_cache'
const DB_VERSION = 1
const STORE_NAME = 'images'

let dbPromise: Promise<IDBDatabase> | null = null

const liveAttachmentUrls = new Map<string, string>()

function openDB(): Promise<IDBDatabase> {
    if (dbPromise) return dbPromise

    dbPromise = new Promise((resolve, reject) => {
        try {
            const req = indexedDB.open(DB_NAME, DB_VERSION)
            req.onupgradeneeded = (event) => {
                const db = (event.target as IDBOpenDBRequest).result
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME)
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

export function getLiveAttachmentUrl(key: string): string {
    return liveAttachmentUrls.get(key) || ''
}

export function setLiveAttachmentUrl(key: string, objectUrl: string): void {
    if (!key || !objectUrl) return
    liveAttachmentUrls.set(key, objectUrl)
}

export async function getCachedAttachmentBlob(key: string): Promise<Blob | null> {
    try {
        const db = await openDB()
        return await new Promise((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly')
                const req = tx.objectStore(STORE_NAME).get(key)
                req.onsuccess = () => resolve((req.result as Blob | null) ?? null)
                req.onerror = () => resolve(null)
            } catch {
                resolve(null)
            }
        })
    } catch {
        return null
    }
}

export async function putCachedAttachmentBlob(key: string, blob: Blob): Promise<void> {
    try {
        const db = await openDB()
        await new Promise<void>((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite')
                tx.objectStore(STORE_NAME).put(blob, key)
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

export async function restoreCachedAttachmentUrl(key: string): Promise<string> {
    const liveUrl = getLiveAttachmentUrl(key)
    if (liveUrl) return liveUrl

    const blob = await getCachedAttachmentBlob(key)
    if (!blob) return ''

    const objectUrl = URL.createObjectURL(blob)
    setLiveAttachmentUrl(key, objectUrl)
    return objectUrl
}

export async function persistObjectUrlToAttachmentCache(key: string, objectUrl: string): Promise<void> {
    if (!key || !objectUrl) return

    try {
        const response = await fetch(objectUrl)
        if (!response.ok) return
        const blob = await response.blob()
        await putCachedAttachmentBlob(key, blob)
        setLiveAttachmentUrl(key, objectUrl)
    } catch {
        /* ignore */
    }
}
