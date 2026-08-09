import { apiFetch } from '../utils/auth'

export const TELEGRAM_LINK_UNAVAILABLE_MESSAGE = 'لینک اتصال تلگرام آماده نشد.'
export const TELEGRAM_LINK_REQUEST_FAILED_MESSAGE = 'ساخت لینک اتصال تلگرام ناموفق بود.'

const TELEGRAM_ORIGIN = 'https://t.me'
const TELEGRAM_BOT_USERNAME_PATTERN = /^[A-Za-z0-9_]{5,32}$/
const TELEGRAM_START_PARAMETER_PATTERN = /^[A-Za-z0-9_-]{1,64}$/
const TELEGRAM_ACCOUNT_LINK_START_PATTERN = /^link_[A-Za-z0-9_-]{1,59}$/

export interface TelegramLinkResponse {
  telegram_linked: boolean
  can_connect_telegram: boolean
  bot_username?: string | null
  telegram_url?: string | null
  start_parameter?: string | null
  expires_at?: string | null
  expires_in?: number | null
  detail?: string | null
}

interface SafeTelegramStartLink {
  href: string
  botUsername: string
  startParameter: string
}

function parseSafeTelegramStartLink(value: unknown): SafeTelegramStartLink | null {
  if (typeof value !== 'string' || !value || value !== value.trim()) return null

  let destination: URL
  try {
    destination = new URL(value)
  } catch {
    return null
  }

  if (
    destination.origin !== TELEGRAM_ORIGIN ||
    destination.protocol !== 'https:' ||
    destination.hostname !== 't.me' ||
    destination.username ||
    destination.password ||
    destination.port ||
    destination.hash
  ) {
    return null
  }

  const botUsername = destination.pathname.slice(1)
  if (
    destination.pathname !== `/${botUsername}` ||
    !TELEGRAM_BOT_USERNAME_PATTERN.test(botUsername)
  ) {
    return null
  }

  const queryEntries = Array.from(destination.searchParams.entries())
  if (queryEntries.length !== 1 || queryEntries[0]?.[0] !== 'start') return null
  const startParameter = queryEntries[0][1]
  if (!TELEGRAM_START_PARAMETER_PATTERN.test(startParameter)) return null

  const canonicalHref = `${TELEGRAM_ORIGIN}/${botUsername}?start=${startParameter}`
  if (destination.href !== canonicalHref) return null

  return { href: canonicalHref, botUsername, startParameter }
}

function isTelegramLinkResponse(value: unknown): value is TelegramLinkResponse {
  if (!value || typeof value !== 'object') return false
  const payload = value as Record<string, unknown>
  return (
    typeof payload.telegram_linked === 'boolean' &&
    typeof payload.can_connect_telegram === 'boolean'
  )
}

function navigateToTelegram(destination: SafeTelegramStartLink): boolean {
  window.location.href = destination.href
  return true
}

export async function requestTelegramLink(): Promise<TelegramLinkResponse> {
  let response: Response
  try {
    response = await apiFetch('/api/auth/telegram-link-token', {
      method: 'POST',
      retryNetwork: false,
    })
  } catch {
    throw new Error(TELEGRAM_LINK_REQUEST_FAILED_MESSAGE)
  }
  const payload = await response.json().catch(() => null)
  if (!response.ok || !isTelegramLinkResponse(payload)) {
    throw new Error(TELEGRAM_LINK_REQUEST_FAILED_MESSAGE)
  }
  return payload
}

export function openTelegramLink(url: string): boolean {
  const destination = parseSafeTelegramStartLink(url)
  return destination ? navigateToTelegram(destination) : false
}

export function openTelegramAccountLink(payload: TelegramLinkResponse): boolean {
  if (
    payload.telegram_linked !== false ||
    payload.can_connect_telegram !== true ||
    typeof payload.bot_username !== 'string' ||
    payload.bot_username !== payload.bot_username.trim() ||
    typeof payload.start_parameter !== 'string' ||
    !TELEGRAM_ACCOUNT_LINK_START_PATTERN.test(payload.start_parameter)
  ) {
    return false
  }

  const destination = parseSafeTelegramStartLink(payload.telegram_url)
  if (
    !destination ||
    destination.botUsername !== payload.bot_username ||
    destination.startParameter !== payload.start_parameter
  ) {
    return false
  }

  return navigateToTelegram(destination)
}
