import {
  formatIranDate,
  getIranHourMinute,
  isTodayInIran,
  isYesterdayInIran,
  parseIranDisplayDate,
} from './iranTime'

type PresenceStatusOptions = {
  now?: Date
  emptyText?: string | null
}

const ONLINE_THRESHOLD_MS = 180_000

export function parseLastSeenAt(lastSeen: string | null | undefined): Date | null {
  return parseIranDisplayDate(lastSeen)
}

export function isUserOnline(lastSeen: string | null | undefined, now: Date = new Date()): boolean {
  const parsed = parseLastSeenAt(lastSeen)
  if (!parsed) return false
  return (now.getTime() - parsed.getTime()) < ONLINE_THRESHOLD_MS
}

export function formatLastSeenStatus(
  lastSeen: string | null | undefined,
  options: PresenceStatusOptions = {},
): string | null {
  const parsed = parseLastSeenAt(lastSeen)
  if (!parsed) return options.emptyText ?? null

  const now = options.now ?? new Date()
  const diffSeconds = Math.floor((now.getTime() - parsed.getTime()) / 1000)

  if (diffSeconds < ONLINE_THRESHOLD_MS / 1000) return 'آنلاین'
  if (diffSeconds < 3600) {
    const mins = Math.floor(diffSeconds / 60)
    return `آخرین بازدید ${mins} دقیقه پیش`
  }

  const isToday = now.getDate() === parsed.getDate()
  const iranHourMinute = getIranHourMinute(parsed)

  if (isTodayInIran(parsed, now)) return `آخرین بازدید امروز ${iranHourMinute}`

  if (isYesterdayInIran(parsed, now)) return `آخرین بازدید دیروز ${iranHourMinute}`

  return `آخرین بازدید ${formatIranDate(parsed)}`
}