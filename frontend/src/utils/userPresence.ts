type PresenceStatusOptions = {
  now?: Date
  emptyText?: string | null
}

const ONLINE_THRESHOLD_MS = 180_000

export function parseLastSeenAt(lastSeen: string | null | undefined): Date | null {
  if (!lastSeen) return null
  const serverStr = lastSeen.endsWith('Z') ? lastSeen : `${lastSeen}Z`
  const parsed = new Date(serverStr)
  return Number.isNaN(parsed.getTime()) ? null : parsed
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
    && now.getMonth() === parsed.getMonth()
    && now.getFullYear() === parsed.getFullYear()

  const hours = parsed.getHours().toString().padStart(2, '0')
  const mins = parsed.getMinutes().toString().padStart(2, '0')

  if (isToday) return `آخرین بازدید امروز ${hours}:${mins}`

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = yesterday.getDate() === parsed.getDate()
    && yesterday.getMonth() === parsed.getMonth()
    && yesterday.getFullYear() === parsed.getFullYear()

  if (isYesterday) return `آخرین بازدید دیروز ${hours}:${mins}`

  return `آخرین بازدید ${parsed.toLocaleDateString('fa-IR')}`
}