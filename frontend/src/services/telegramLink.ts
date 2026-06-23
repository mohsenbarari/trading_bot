import { apiFetch } from '../utils/auth'

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

export async function requestTelegramLink(): Promise<TelegramLinkResponse> {
  const response = await apiFetch('/api/auth/telegram-link-token', { method: 'POST' })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : 'ساخت لینک اتصال تلگرام ناموفق بود.'
    throw new Error(detail)
  }
  return payload as TelegramLinkResponse
}

export function openTelegramLink(url: string) {
  window.location.href = url
}
