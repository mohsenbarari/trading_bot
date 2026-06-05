import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getChannelCandidatesCacheKey,
  getChannelListCacheKey,
  getChannelMembersCacheKey,
  getGroupDetailCacheKey,
  invalidateChatManagerCache,
  readChatManagerCache,
  writeChatManagerCache,
} from './chatManagerCache'

describe('chatManagerCache', () => {
  afterEach(() => {
    invalidateChatManagerCache()
    vi.useRealTimers()
  })

  it('returns cached manager values until ttl expires', () => {
    vi.useFakeTimers()
    writeChatManagerCache('group:1:detail', { value: 1 }, 1000)

    expect(readChatManagerCache('group:1:detail')).toEqual({ value: 1 })

    vi.advanceTimersByTime(1001)

    expect(readChatManagerCache('group:1:detail')).toBeNull()
  })

  it('invalidates by prefix and exposes stable manager keys', () => {
    const groupKey = getGroupDetailCacheKey(7)
    const channelListKey = getChannelListCacheKey()
    const channelMembersKey = getChannelMembersCacheKey(9)
    const channelCandidatesKey = getChannelCandidatesCacheKey(9, '  Ali  ')

    writeChatManagerCache(groupKey, 'group')
    writeChatManagerCache(channelListKey, 'channels')
    writeChatManagerCache(channelMembersKey, 'members')
    writeChatManagerCache(channelCandidatesKey, 'candidates')

    invalidateChatManagerCache('channel:9:')

    expect(readChatManagerCache(groupKey)).toBe('group')
    expect(readChatManagerCache(channelListKey)).toBe('channels')
    expect(readChatManagerCache(channelMembersKey)).toBeNull()
    expect(readChatManagerCache(channelCandidatesKey)).toBeNull()
  })
})
