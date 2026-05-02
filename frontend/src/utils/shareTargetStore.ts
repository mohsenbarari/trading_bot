// Reader for entries the share-target service worker stashed in IndexedDB.
// Mirrors the schema written by frontend/public/share-target-sw.js.

const SHARE_DB_NAME = 'trading-bot-share-target'
const SHARE_DB_VERSION = 1
const SHARE_STORE = 'pending'

export type SharedFileEntry = {
  name: string
  type: string
  size: number
  blob: Blob
}

export type SharedPayload = {
  key: string
  createdAt: number
  title: string
  text: string
  url: string
  files: SharedFileEntry[]
}

function openShareDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SHARE_DB_NAME, SHARE_DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(SHARE_STORE)) {
        db.createObjectStore(SHARE_STORE, { keyPath: 'key' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

export async function readSharedPayload(key: string): Promise<SharedPayload | null> {
  try {
    const db = await openShareDB()
    return await new Promise<SharedPayload | null>((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readonly')
      const req = tx.objectStore(SHARE_STORE).get(key)
      req.onsuccess = () => {
        db.close()
        resolve((req.result as SharedPayload) || null)
      }
      req.onerror = () => { db.close(); reject(req.error) }
    })
  } catch {
    return null
  }
}

export async function deleteSharedPayload(key: string): Promise<void> {
  try {
    const db = await openShareDB()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readwrite')
      tx.objectStore(SHARE_STORE).delete(key)
      tx.oncomplete = () => { db.close(); resolve() }
      tx.onerror = () => { db.close(); reject(tx.error) }
    })
  } catch {
    /* noop */
  }
}
