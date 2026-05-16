import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  buildQuickMessageReactions,
  getRecentMessageReactions,
  MESSAGE_REACTION_CATALOG,
  QUICK_MESSAGE_REACTIONS,
  recordRecentMessageReaction,
} from './messageReactions'

describe('messageReactions', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('returns only supported recent reactions from storage and tolerates malformed payloads', () => {
    window.localStorage.setItem('chat_recent_message_reactions', JSON.stringify(['🔥', 'bad', '🙏']))
    expect(getRecentMessageReactions()).toEqual(['🔥', '🙏'])

    window.localStorage.setItem('chat_recent_message_reactions', '{not-json')
    expect(getRecentMessageReactions()).toEqual([])
  })

  it('records supported reactions once and ignores unsupported entries', () => {
    recordRecentMessageReaction('🔥')
    recordRecentMessageReaction('🙏')
    recordRecentMessageReaction('🔥')
    recordRecentMessageReaction('not-supported')

    expect(getRecentMessageReactions()).toEqual(['🔥', '🙏'])
  })

  it('gracefully ignores storage write failures', () => {
    const setItemSpy = vi.spyOn(window.localStorage.__proto__, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })

    expect(() => recordRecentMessageReaction('🔥')).not.toThrow()
    expect(setItemSpy).toHaveBeenCalled()
  })

  it('builds the quick reaction row from current, recent, defaults, and available reactions', () => {
    window.localStorage.setItem('chat_recent_message_reactions', JSON.stringify(['🙏', '🔥', '😁']))

    const quick = buildQuickMessageReactions(
      ['🔥', '🙏', '😁', '👎', '💯', '👍', 'unsupported'],
      '💯',
    )

    expect(quick[0]).toBe('💯')
    expect(quick).toContain('🙏')
    expect(quick).toContain('🔥')
    expect(quick).toContain('👍')
    expect(quick).toHaveLength(6)
    expect(quick.every((emoji) => MESSAGE_REACTION_CATALOG.includes(emoji as any))).toBe(true)
    expect(QUICK_MESSAGE_REACTIONS).toHaveLength(6)
  })
})