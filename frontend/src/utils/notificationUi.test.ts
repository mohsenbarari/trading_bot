import { describe, expect, it } from 'vitest'
import {
  AlertCircle,
  AlertTriangle,
  Bell,
  CheckCircle,
  MessageCircle,
  ShieldAlert,
} from 'lucide-vue-next'
import { getNotificationIconComponent } from './notificationUi'

describe('notificationUi', () => {
  it('maps each notification display kind to the matching icon component', () => {
    expect(getNotificationIconComponent({ kind: 'chat' })).toBe(MessageCircle)
    expect(getNotificationIconComponent({ category: 'system' })).toBe(ShieldAlert)
    expect(getNotificationIconComponent({ category: 'trade', level: 'success' })).toBe(CheckCircle)
    expect(getNotificationIconComponent({ category: 'user', level: 'warning' })).toBe(AlertTriangle)
    expect(getNotificationIconComponent({ category: 'user', level: 'error' })).toBe(AlertCircle)
    expect(getNotificationIconComponent({ category: 'user', level: 'info' })).toBe(Bell)
  })
})