import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConversationsStore } from './conversations'
import type { Conversation } from '../../types/chat'

const cacheMocks = vi.hoisted(() => ({
  cachedPayload: null as any,
  writeCachedConversations: vi.fn(async () => null),
}))

vi.mock('../../services/chat/chatCacheRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/chat/chatCacheRepository')>()
  return {
    ...actual,
    readCachedConversations: vi.fn(async () => cacheMocks.cachedPayload),
    writeCachedConversations: cacheMocks.writeCachedConversations,
  }
})

function conversation(id: number, overrides: Partial<Conversation> = {}): Conversation {
  return {
    id,
    other_user_id: id,
    other_user_name: `کاربر ${id}`,
    last_message_content: null,
    last_message_type: null,
    last_message_at: null,
    unread_count: 0,
    ...overrides,
  }
}

describe('useConversationsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    cacheMocks.cachedPayload = null
    cacheMocks.writeCachedConversations.mockClear()
  })

  it('hydrates conversations from cache before server reconciliation', async () => {
    cacheMocks.cachedPayload = {
      conversations: [conversation(10, { other_user_name: 'کش شده' })],
      pendingMutations: [],
      cachedAt: 123,
      schemaVersion: 1,
    }

    const store = useConversationsStore()
    const cached = await store.hydrateFromCache(7)

    expect(cached?.conversations[0].other_user_name).toBe('کش شده')
    expect(store.conversations[0].other_user_name).toBe('کش شده')
    expect(store.hydrationStatus).toBe('cached')
    expect(store.lastCacheAt).toBe(123)
  })

  it('merges pending mute and pin state instead of blindly overwriting from server', async () => {
    const store = useConversationsStore()
    store.addPendingMutation({
      id: 'mute:10',
      roomKey: 10,
      kind: 'mute',
      payload: { muted: true },
      createdAt: 1,
    })
    store.addPendingMutation({
      id: 'pin:10',
      roomKey: 10,
      kind: 'pin',
      payload: { pinned: true },
      createdAt: 2,
    })

    const merged = await store.replaceFromServer(7, [
      conversation(10, { is_muted: false, is_pinned: false }),
    ])

    expect(merged[0].is_muted).toBe(true)
    expect(merged[0].is_pinned).toBe(true)
    expect(cacheMocks.writeCachedConversations).toHaveBeenCalledOnce()
  })
})

