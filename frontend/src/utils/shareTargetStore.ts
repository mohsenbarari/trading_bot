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

type StoredSharedFileEntry = {
  name?: string
  type?: string
  size?: number
  blob?: Blob | null
  bodyBase64?: string
  bytes?: number[] | ArrayBuffer | ArrayBufferView
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

function decodeBase64(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

function normalizeStoredBytes(file: StoredSharedFileEntry): Uint8Array | null {
  if (typeof file.bodyBase64 === 'string' && file.bodyBase64.length > 0) {
    return decodeBase64(file.bodyBase64)
  }

  if (Array.isArray(file.bytes)) {
    return Uint8Array.from(file.bytes)
  }

  if (file.bytes instanceof ArrayBuffer) {
    return new Uint8Array(file.bytes)
  }

  if (ArrayBuffer.isView(file.bytes)) {
    return new Uint8Array(file.bytes.buffer, file.bytes.byteOffset, file.bytes.byteLength)
  }

  return null
}

function normalizeSharedFileEntry(file: StoredSharedFileEntry): SharedFileEntry | null {
  const name = typeof file.name === 'string' && file.name.length > 0 ? file.name : 'shared'
  const type = typeof file.type === 'string' && file.type.length > 0 ? file.type : 'application/octet-stream'

  if (file.blob instanceof Blob) {
    return {
      name,
      type,
      size: Number(file.size || file.blob.size || 0),
      blob: file.blob,
    }
  }

  const bytes = normalizeStoredBytes(file)
  if (!bytes) {
    return null
  }

  return {
    name,
    type,
    size: Number(file.size || bytes.byteLength),
    blob: new Blob([bytes], { type }),
  }
}

export async function readSharedPayload(key: string): Promise<SharedPayload | null> {
  try {
    const db = await openShareDB()
    return await new Promise<SharedPayload | null>((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readonly')
      const req = tx.objectStore(SHARE_STORE).get(key)
      req.onsuccess = () => {
        db.close()
        const result = req.result as (SharedPayload & { files?: StoredSharedFileEntry[] }) | null
        if (!result) {
          resolve(null)
          return
        }

        const files = Array.isArray(result.files)
          ? result.files.map((file) => normalizeSharedFileEntry(file)).filter((file): file is SharedFileEntry => file !== null)
          : []

        resolve({
          ...result,
          files,
        })
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
