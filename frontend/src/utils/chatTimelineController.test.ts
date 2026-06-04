import { describe, expect, it } from 'vitest'
import type { Message } from '../types/chat'
import {
  buildMessengerConversationQuery,
  createMessengerTimelineCache,
  getAlbumMessagesForMessage,
  getContextMenuMessageIds,
  getRouteQueryValue,
  groupMessengerMessages,
  sortMessageIdsByMessageOrder,
  toggleMessageSelectionBatch,
} from './chatTimelineController'

function message(overrides: Partial<Message>): Message {
  return {
    id: 1,
    sender_id: 10,
    receiver_id: 20,
    content: 'hello',
    message_type: 'text',
    is_read: false,
    created_at: '2026-05-30T08:00:00',
    ...overrides,
  }
}

function albumContent(albumId: string, albumIndex: number) {
  return JSON.stringify({ file_id: `file-${albumIndex}`, album_id: albumId, album_index: albumIndex })
}

describe('chatTimelineController', () => {
  it('normalizes route query values and preserves unrelated query params', () => {
    expect(getRouteQueryValue(['42', '43'])).toBe('42')
    expect(getRouteQueryValue(null)).toBe('')

    expect(buildMessengerConversationQuery({ tab: 'chat', user_id: '1', user_name: 'Old' }, -9, 'Room')).toEqual({
      tab: 'chat',
      user_id: '-9',
      user_name: 'Room',
    })
    expect(buildMessengerConversationQuery({ tab: 'chat', empty: '', user_id: '1' }, null, '')).toEqual({ tab: 'chat' })
  })

  it('sorts and toggles selection batches by visible message order', () => {
    const ordered = [message({ id: 10 }), message({ id: 20 }), message({ id: 30 })]

    expect(sortMessageIdsByMessageOrder([30, 10, 30, Number.NaN], ordered)).toEqual([10, 30])

    const added = toggleMessageSelectionBatch([20], [30, 10], ordered)
    expect(added).toEqual({ selectedMessageIds: [10, 20, 30], cleared: false })

    const removed = toggleMessageSelectionBatch([10, 20], [20, 10], ordered)
    expect(removed).toEqual({ selectedMessageIds: [], cleared: true })
  })

  it('builds explicit album context menu ids and stable grouped timeline wrappers', () => {
    const first = message({ id: 1, message_type: 'image', content: albumContent('batch-a', 1) })
    const second = message({ id: 2, message_type: 'image', content: albumContent('batch-a', 0), created_at: '2026-05-30T08:00:01' })
    const reply = message({ id: 3, message_type: 'image', content: albumContent('batch-a', 2), reply_to_message: { id: 99, sender_id: 20, content: 'x', message_type: 'text' } })
    const text = message({ id: 4, content: 'not media', created_at: '2026-05-31T08:00:00' })
    const allMessages = [first, second, reply, text]

    expect(getAlbumMessagesForMessage(first, allMessages).map(item => item.id)).toEqual([2, 1])
    expect(getContextMenuMessageIds(first, allMessages)).toEqual([2, 1])

    const cache = createMessengerTimelineCache()
    const grouped = groupMessengerMessages(allMessages, date => date.slice(0, 10), cache)
    expect(grouped).toHaveLength(2)
    expect(grouped[0]?.items).toHaveLength(2)
    expect(grouped[0]?.items[0]).toMatchObject({ type: 'album', id: 'album_batch-a' })
    expect(grouped[0]?.items[1]).toMatchObject({ id: 3 })

    const regrouped = groupMessengerMessages(allMessages, date => date.slice(0, 10), cache)
    expect(regrouped[0]).toBe(grouped[0])
    expect(regrouped[0]?.items[0]).toBe(grouped[0]?.items[0])
  })
})