import { describe, expect, it } from 'vitest'

import { formatLastSeenStatus, isUserOnline, parseLastSeenAt } from './userPresence'

describe('userPresence', () => {
  it('parses timezone-naive server timestamps as UTC', () => {
    const parsed = parseLastSeenAt('2026-05-22T08:00:00')
    expect(parsed?.toISOString()).toBe('2026-05-22T08:00:00.000Z')
  })

  it('returns online status for fresh timestamps', () => {
    const now = new Date('2026-05-22T08:03:00Z')
    expect(isUserOnline('2026-05-22T08:01:30Z', now)).toBe(true)
    expect(formatLastSeenStatus('2026-05-22T08:01:30Z', { now })).toBe('آنلاین')
  })

  it('formats minute, today, yesterday, and older statuses', () => {
    const now = new Date('2026-05-22T12:00:00Z')
    expect(formatLastSeenStatus('2026-05-22T11:55:00Z', { now })).toBe('آخرین بازدید 5 دقیقه پیش')
    expect(formatLastSeenStatus('2026-05-22T09:15:00Z', { now })).toBe('آخرین بازدید امروز 09:15')
    expect(formatLastSeenStatus('2026-05-21T20:30:00Z', { now })).toBe('آخرین بازدید دیروز 20:30')
    expect(formatLastSeenStatus('2026-05-18T08:00:00Z', { now })).toContain('آخرین بازدید')
  })

  it('returns configurable empty fallback text', () => {
    expect(formatLastSeenStatus(null, { emptyText: null })).toBeNull()
    expect(formatLastSeenStatus(undefined, { emptyText: 'آخرین بازدید خیلی وقت پیش' })).toBe('آخرین بازدید خیلی وقت پیش')
  })

  it('returns null for invalid timestamps', () => {
    expect(parseLastSeenAt('not-a-date')).toBeNull()
  })
})