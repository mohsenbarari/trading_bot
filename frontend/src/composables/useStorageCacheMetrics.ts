import localforage from 'localforage'

const fileCacheStore = localforage.createInstance({
  name: 'trading-bot-chat-files',
  storeName: 'files',
  description: 'Cached chat document/file blobs for offline reuse and Web Share.',
})

export class StorageCacheSizeUnavailableError extends Error {
  constructor() {
    super('Local file cache size is unavailable.')
    this.name = 'StorageCacheSizeUnavailableError'
  }
}

export class StorageCacheClearUnavailableError extends Error {
  constructor() {
    super('Local file cache could not be cleared.')
    this.name = 'StorageCacheClearUnavailableError'
  }
}

function cachedBlob(value: unknown): Blob | null {
  if (!value || typeof value !== 'object') return null
  const blob = (value as { blob?: unknown }).blob
  return blob instanceof Blob ? blob : null
}

export async function getStorageCacheBytes(): Promise<number> {
  let total = 0

  try {
    await fileCacheStore.iterate<unknown, void>((value) => {
      const blob = cachedBlob(value)
      if (blob) total += blob.size
    })
  } catch {
    // Keep provider details private while preserving a distinct failure signal.
    throw new StorageCacheSizeUnavailableError()
  }

  return total
}

export async function getStorageCacheSize(): Promise<string> {
  const bytes = await getStorageCacheBytes()
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export async function clearStorageFileCache(): Promise<void> {
  try {
    await fileCacheStore.clear()
  } catch {
    throw new StorageCacheClearUnavailableError()
  }
}

export function reloadAfterStorageCacheClear(): void {
  // A full reload also drops the protected Messenger module's in-memory Blob
  // references and object URLs without changing that frozen runtime module.
  window.location.reload()
}
