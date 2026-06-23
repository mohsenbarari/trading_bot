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

  it('normalizes persisted extra_payload metadata into the same shape as realtime payloads', () => {
    const normalized = normalizeAppNotificationPayload({
      id: 91,
      message: 'trade body',
      category: 'trade',
      extra_payload: {
        route: '/users/19?account_name=ali',
        trade_number: 10025,
        recipient_role: 'offer_owner',
      },
    })

    expect(normalized.route).toBe('/users/19?account_name=ali')
    expect(normalized.trade_number).toBe(10025)
    expect(normalized.recipient_role).toBe('offer_owner')
    expect(normalized.message).toBe('trade body')
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

  it('preserves explicit payload text and supports user titles plus chat-kind toasts', () => {
    const normalized = normalizeAppNotificationPayload({
      id: 'room-alert',
      title: 'عنوان سفارشی',
      body: 'fallback body',
      content: 'rich body',
      message: '   ',
      category: 'USER',
      level: 'UNKNOWN',
    })

    expect(normalized.id).toBe('room-alert')
    expect(normalized.title).toBe('عنوان سفارشی')
    expect(normalized.body).toBe('rich body')
    expect(normalized.content).toBe('rich body')
    expect(normalized.message).toBe('rich body')
    expect(buildNotificationTitle('user')).toBe('اعلان کاربری')

    const chatToast = createToastNotification({ title: 'chat', body: 'toast', kind: 'chat' })
    expect(chatToast.kind).toBe('chat')
  })

  it('falls back for unknown title categories and non-string notification ids', () => {
    expect(typeof normalizeNotificationId(null)).toBe('number')
    expect(buildNotificationTitle('unknown' as any)).toBe('اعلان جدید')
  })
})
