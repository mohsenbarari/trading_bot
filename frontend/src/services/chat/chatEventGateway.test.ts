import { describe, expect, it, vi } from 'vitest'
import { createChatEventGateway } from './chatEventGateway'

describe('ChatEventGateway', () => {
  it('normalizes message events into message and conversation store mutations', () => {
    const appendOrReplaceMessage = vi.fn()
    const patchConversation = vi.fn()
    const gateway = createChatEventGateway({
      messages: { appendOrReplaceMessage },
      conversations: { patchConversation },
    })

    const result = gateway.dispatch('chat:message', {
      id: 44,
      sender_id: 2,
      receiver_id: 9,
      content: 'سلام',
      message_type: 'text',
      created_at: '2026-06-04T20:00:00Z',
    })

    expect(result).toMatchObject({ handled: true, roomKey: 2 })
    expect(appendOrReplaceMessage).toHaveBeenCalledWith(2, expect.objectContaining({ id: 44 }))
    expect(patchConversation).toHaveBeenCalledWith(2, expect.objectContaining({
      last_message_type: 'text',
    }))
  })

  it('routes typing events into session store state', () => {
    const setUserTyping = vi.fn()
    const gateway = createChatEventGateway({
      session: { setUserTyping },
    })

    gateway.dispatch('chat:typing', {
      sender_id: 8,
      receiver_id: 12,
      sender_name: 'زهرا',
      active: true,
    })

    expect(setUserTyping).toHaveBeenCalledWith(8, 8, true, 'زهرا')
  })

  it('ignores stale message replacements and does not regress conversation previews', () => {
    const appendOrReplaceMessage = vi.fn()
    const patchConversation = vi.fn()
    const gateway = createChatEventGateway({
      messages: { appendOrReplaceMessage },
      conversations: { patchConversation },
    })

    gateway.dispatch('chat:message', {
      id: 50,
      sender_id: 2,
      receiver_id: 9,
      content: 'جدید',
      message_type: 'text',
      created_at: '2026-06-05T10:00:02Z',
      version: 2,
    })
    const staleResult = gateway.dispatch('chat:message', {
      id: 50,
      sender_id: 2,
      receiver_id: 9,
      content: 'قدیمی',
      message_type: 'text',
      created_at: '2026-06-05T10:00:01Z',
      version: 1,
    })

    expect(staleResult).toMatchObject({ handled: false, reason: 'stale-message' })
    expect(appendOrReplaceMessage).toHaveBeenCalledTimes(1)
    expect(patchConversation).toHaveBeenCalledTimes(1)
    expect(patchConversation).toHaveBeenLastCalledWith(2, expect.objectContaining({
      last_message_content: 'جدید',
    }))
  })

  it('keeps the newest room preview when older message events arrive later', () => {
    const patchConversation = vi.fn()
    const gateway = createChatEventGateway({
      messages: { appendOrReplaceMessage: vi.fn() },
      conversations: { patchConversation },
    })

    gateway.dispatch('chat:message', {
      id: 61,
      sender_id: 3,
      receiver_id: 9,
      content: 'آخرین پیام',
      message_type: 'text',
      created_at: '2026-06-05T10:00:10Z',
    })
    gateway.dispatch('chat:message', {
      id: 60,
      sender_id: 3,
      receiver_id: 9,
      content: 'پیام قدیمی‌تر',
      message_type: 'text',
      created_at: '2026-06-05T10:00:05Z',
    })

    expect(patchConversation).toHaveBeenCalledTimes(1)
    expect(patchConversation).toHaveBeenLastCalledWith(3, expect.objectContaining({
      last_message_content: 'آخرین پیام',
    }))
  })

  it('ignores stale reaction updates by event clock', () => {
    const patchReaction = vi.fn()
    const gateway = createChatEventGateway({
      messages: { patchReaction },
    })

    gateway.dispatch('chat:reaction', {
      id: 88,
      sender_id: 2,
      receiver_id: 9,
      reactions: [{ emoji: '🔥', user_id: 4 }],
      updated_at: '2026-06-05T10:00:02Z',
    })
    const staleResult = gateway.dispatch('chat:reaction', {
      id: 88,
      sender_id: 2,
      receiver_id: 9,
      reactions: [{ emoji: '👍', user_id: 4 }],
      updated_at: '2026-06-05T10:00:01Z',
    })

    expect(staleResult).toMatchObject({ handled: false, reason: 'stale-reaction' })
    expect(patchReaction).toHaveBeenCalledTimes(1)
    expect(patchReaction).toHaveBeenCalledWith(88, [{ emoji: '🔥', user_id: 4 }])
  })

  it('rejects malformed events without mutating stores', () => {
    const appendOrReplaceMessage = vi.fn()
    const gateway = createChatEventGateway({
      messages: { appendOrReplaceMessage },
    })

    const result = gateway.dispatch('chat:message', { content: 'bad' })

    expect(result.handled).toBe(false)
    expect(appendOrReplaceMessage).not.toHaveBeenCalled()
  })
})
