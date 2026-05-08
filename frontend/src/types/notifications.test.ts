import { describe, expect, it } from 'vitest'
import {
  buildNotificationTitle,
  createToastNotification,
  getNotificationDisplayKind,
  normalizeAppNotificationPayload,
  normalizeNotificationCategory,
  normalizeNotificationId,
  normalizeNotificationLevel,
} from './notifications'

describe('notification type helpers', () => {
  it('normalizes ids, categories, and levels defensively', () => {
    expect(normalizeNotificationId(42)).toBe(42)
    expect(normalizeNotificationId('17')).toBe(17)
    expect(typeof normalizeNotificationId('   ')).toBe('number')

    expect(normalizeNotificationCategory('USER')).toBe('user')
    expect(normalizeNotificationCategory('unknown')).toBe('system')
    expect(normalizeNotificationLevel('WARNING')).toBe('warning')
    expect(normalizeNotificationLevel('unknown')).toBe('info')
  })

  it('fills in notification payload defaults from message/category data', () => {
    const normalized = normalizeAppNotificationPayload({
      id: '88',
      message: 'system body',
      category: 'trade',
      level: 'SUCCESS',
    })

    expect(normalized.id).toBe(88)
    expect(normalized.title).toBe(buildNotificationTitle('trade'))
    expect(normalized.body).toBe('system body')
    expect(normalized.content).toBe('system body')
    expect(normalized.message).toBe('system body')
    expect(normalized.category).toBe('trade')
    expect(normalized.level).toBe('success')
  })

  it('creates toast ids and resolves display kinds for chat, system, and level-driven items', () => {
    const toast = createToastNotification({ title: 't', body: 'b' })
    expect(typeof toast.id).toBe('number')
    expect(toast.kind).toBe('app')

    expect(getNotificationDisplayKind({ kind: 'chat' })).toBe('chat')
    expect(getNotificationDisplayKind({ category: 'system' })).toBe('system')
    expect(getNotificationDisplayKind({ category: 'user', level: 'error' })).toBe('error')
    expect(getNotificationDisplayKind({ category: 'user' })).toBe('info')
  })
})