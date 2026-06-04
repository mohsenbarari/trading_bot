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

