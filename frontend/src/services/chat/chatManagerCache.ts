const MANAGER_CACHE_TTL_MS = 8000

type CacheEntry<T> = {
  expiresAt: number
  value: T
}

const cache = new Map<string, CacheEntry<unknown>>()

function now() {
  return Date.now()
}

export function readChatManagerCache<T>(key: string): T | null {
  const entry = cache.get(key)
  if (!entry) return null
  if (entry.expiresAt <= now()) {
    cache.delete(key)
    return null
  }
  return entry.value as T
}

export function writeChatManagerCache<T>(key: string, value: T, ttlMs = MANAGER_CACHE_TTL_MS) {
  cache.set(key, {
    value,
    expiresAt: now() + ttlMs,
  })
}

export function invalidateChatManagerCache(prefix?: string) {
  if (!prefix) {
    cache.clear()
    return
  }

  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key)
    }
  }
}

export function getGroupDetailCacheKey(groupId: number) {
  return `group:${groupId}:detail`
}

export function getChannelListCacheKey() {
  return 'channel:list'
}

export function getChannelMembersCacheKey(channelId: number) {
  return `channel:${channelId}:members`
}

export function getChannelCandidatesCacheKey(channelId: number, query: string) {
  return `channel:${channelId}:candidates:${query.trim().toLowerCase()}`
}
