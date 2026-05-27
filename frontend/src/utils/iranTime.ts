const IRAN_TIME_ZONE = 'Asia/Tehran'

const ISO_TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T/
const ISO_WITH_ZONE_RE = /(Z|[+-]\d{2}:?\d{2})$/i
const formatterCache = new Map<string, Intl.DateTimeFormat>()

type DateInput = string | number | Date | null | undefined

function normalizeDateInput(value: DateInput): Date | null {
  if (value == null) return null

  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }

  if (typeof value === 'number') {
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
  }

  const trimmed = value.trim()
  if (!trimmed) return null

  const normalized = ISO_TIMESTAMP_RE.test(trimmed) && !ISO_WITH_ZONE_RE.test(trimmed)
    ? `${trimmed}Z`
    : trimmed

  const parsed = new Date(normalized)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function buildFormatter(
  locale: string,
  options: Intl.DateTimeFormatOptions,
) {
  const resolvedOptions = {
    ...options,
    timeZone: IRAN_TIME_ZONE,
  }
  const cacheKey = `${locale}:${JSON.stringify(
    Object.entries(resolvedOptions).sort(([left], [right]) => left.localeCompare(right)),
  )}`
  const cached = formatterCache.get(cacheKey)
  if (cached) return cached

  const formatter = new Intl.DateTimeFormat(locale, resolvedOptions)
  formatterCache.set(cacheKey, formatter)
  return formatter
}

function getIranDateParts(value: DateInput) {
  const parsed = normalizeDateInput(value)
  if (!parsed) return null

  const parts = buildFormatter('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(parsed)

  const year = parts.find((part) => part.type === 'year')?.value
  const month = parts.find((part) => part.type === 'month')?.value
  const day = parts.find((part) => part.type === 'day')?.value
  if (!year || !month || !day) return null

  return { year, month, day }
}

export function parseIranDisplayDate(value: DateInput): Date | null {
  return normalizeDateInput(value)
}

export function formatIranTime(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' },
  locale = 'fa-IR',
): string {
  const parsed = normalizeDateInput(value)
  if (!parsed) return ''
  return buildFormatter(locale, options).format(parsed)
}

export function formatIranDate(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'long', day: 'numeric' },
  locale = 'fa-IR',
): string {
  const parsed = normalizeDateInput(value)
  if (!parsed) return ''
  return buildFormatter(locale, options).format(parsed)
}

export function formatIranDateTime(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' },
  locale = 'fa-IR',
): string {
  const parsed = normalizeDateInput(value)
  if (!parsed) return ''
  return buildFormatter(locale, options).format(parsed)
}

export function getIranHour(value: DateInput = new Date()): number {
  const parsed = normalizeDateInput(value)
  if (!parsed) return 0
  const hour = buildFormatter('en-GB', {
    hour: '2-digit',
    hourCycle: 'h23',
  }).format(parsed)
  return Number(hour)
}

export function isSameIranDay(left: DateInput, right: DateInput): boolean {
  const leftParts = getIranDateParts(left)
  const rightParts = getIranDateParts(right)
  if (!leftParts || !rightParts) return false
  return leftParts.year === rightParts.year
    && leftParts.month === rightParts.month
    && leftParts.day === rightParts.day
}

export function isTodayInIran(value: DateInput, now: DateInput = new Date()): boolean {
  return isSameIranDay(value, now)
}

export function isYesterdayInIran(value: DateInput, now: DateInput = new Date()): boolean {
  const parsedNow = normalizeDateInput(now)
  if (!parsedNow) return false
  const yesterday = new Date(parsedNow.getTime() - 24 * 60 * 60 * 1000)
  return isSameIranDay(value, yesterday)
}

export function getIranHourMinute(value: DateInput): string {
  return formatIranTime(value, { hour: '2-digit', minute: '2-digit' })
}

export { IRAN_TIME_ZONE }