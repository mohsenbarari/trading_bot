import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearCachedConversations,
  getChatCacheKey,
  readCachedConversations,
  writeCachedConversations,
} from './chatCacheRepository'

describe('chatCacheRepository', () => {
  beforeEach(() => {
    localStorage.clear()
    ;(globalThis as any).indexedDB = undefined
  })

  it('keys cached data by user and schema version', () => {
    expect(getChatCacheKey({ userId: 5, schemaVersion: 2 })).toBe('u:5:schema:2')
  })

  it('uses a safe localStorage fallback when IndexedDB is unavailable', async () => {
    await writeCachedConversations({ userId: 5, schemaVersion: 1 }, [{
      id: 1,
      other_user_id: 9,
      other_user_name: 'علی',
      last_message_content: null,
      last_message_type: null,
      last_message_at: null,
      unread_count: 0,
    }], [])

    const cached = await readCachedConversations({ userId: 5, schemaVersion: 1 })
    expect(cached?.conversations[0].other_user_name).toBe('علی')

    await clearCachedConversations({ userId: 5, schemaVersion: 1 })
    expect(await readCachedConversations({ userId: 5, schemaVersion: 1 })).toBeNull()
  })
})

