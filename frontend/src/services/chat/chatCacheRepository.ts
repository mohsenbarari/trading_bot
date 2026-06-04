import type { Conversation } from '../../types/chat'

export const CHAT_CACHE_SCHEMA_VERSION = 1

export interface ChatCacheIdentity {
  userId: number | string
  schemaVersion?: number
}

export interface ChatPendingLocalMutation {
  id: string
  roomKey?: number | null
  kind: 'optimistic-message' | 'upload' | 'mute' | 'pin' | 'archive' | 'read' | 'conversation-order'
  payload: Record<string, unknown>
  createdAt: number
}

export interface CachedConversationsPayload {
  conversations: Conversation[]
  pendingMutations: ChatPendingLocalMutation[]
  cachedAt: number
  schemaVersion: number
}

const DB_NAME = 'messenger-chat-cache'
const STORE_NAME = 'chat-cache'
const FALLBACK_PREFIX = 'messenger:chat-cache:'

function getSchemaVersion(identity: ChatCacheIdentity) {
  return identity.schemaVersion ?? CHAT_CACHE_SCHEMA_VERSION
}

export function getChatCacheKey(identity: ChatCacheIdentity) {
  return `u:${identity.userId}:schema:${getSchemaVersion(identity)}`
}

function canUseIndexedDb() {
  return typeof indexedDB !== 'undefined'
}

function openCacheDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)

    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function withCacheStore<T>(
  mode: IDBTransactionMode,
  callback: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openCacheDb()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, mode)
    const store = transaction.objectStore(STORE_NAME)
    const request = callback(store)

    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
    transaction.oncomplete = () => db.close()
    transaction.onerror = () => {
      db.close()
      reject(transaction.error)
    }
  })
}

function readFallback(key: string): CachedConversationsPayload | null {
  if (typeof localStorage === 'undefined') return null

  try {
    const raw = localStorage.getItem(`${FALLBACK_PREFIX}${key}`)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeFallback(key: string, payload: CachedConversationsPayload) {
  if (typeof localStorage === 'undefined') return

  try {
    localStorage.setItem(`${FALLBACK_PREFIX}${key}`, JSON.stringify(payload))
  } catch {
    // Cache writes are best-effort and must not affect Messenger runtime.
  }
}

function removeFallback(key: string) {
  if (typeof localStorage === 'undefined') return

  try {
    localStorage.removeItem(`${FALLBACK_PREFIX}${key}`)
  } catch {
    // Cache cleanup is best-effort.
  }
}

export async function readCachedConversations(
  identity: ChatCacheIdentity,
): Promise<CachedConversationsPayload | null> {
  const key = getChatCacheKey(identity)

  if (!canUseIndexedDb()) {
    return readFallback(key)
  }

  try {
    return await withCacheStore<CachedConversationsPayload | undefined>('readonly', (store) => store.get(key)) ?? null
  } catch {
    return readFallback(key)
  }
}

export async function writeCachedConversations(
  identity: ChatCacheIdentity,
  conversations: Conversation[],
  pendingMutations: ChatPendingLocalMutation[] = [],
) {
  const key = getChatCacheKey(identity)
  const payload: CachedConversationsPayload = {
    conversations,
    pendingMutations,
    cachedAt: Date.now(),
    schemaVersion: getSchemaVersion(identity),
  }

  writeFallback(key, payload)

  if (!canUseIndexedDb()) {
    return payload
  }

  try {
    await withCacheStore<IDBValidKey>('readwrite', (store) => store.put(payload, key))
  } catch {
    // Fallback already contains the payload.
  }

  return payload
}

export async function clearCachedConversations(identity: ChatCacheIdentity) {
  const key = getChatCacheKey(identity)
  removeFallback(key)

  if (!canUseIndexedDb()) return

  try {
    await withCacheStore<undefined>('readwrite', (store) => store.delete(key))
  } catch {
    // Cache cleanup is best-effort.
  }
}

