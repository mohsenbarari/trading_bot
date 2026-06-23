import { beforeEach, describe, expect, it, vi } from 'vitest'
import { requestTelegramLink } from './telegramLink'

const telegramLinkServiceMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: telegramLinkServiceMocks.apiFetchMock,
}))

function responseOf(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  } as Response
}

describe('telegramLink service', () => {
  beforeEach(() => {
    telegramLinkServiceMocks.apiFetchMock.mockReset()
  })

  it('requests Telegram link tokens without global network retry', async () => {
    telegramLinkServiceMocks.apiFetchMock.mockResolvedValue(responseOf({
      telegram_linked: false,
      can_connect_telegram: true,
      telegram_url: 'https://t.me/example_bot?start=link_token',
    }))

    await expect(requestTelegramLink()).resolves.toMatchObject({
      telegram_url: 'https://t.me/example_bot?start=link_token',
    })
    expect(telegramLinkServiceMocks.apiFetchMock).toHaveBeenCalledWith(
      '/api/auth/telegram-link-token',
      { method: 'POST', retryNetwork: false },
    )
  })

  it('turns transport failures into a user-facing Telegram link error', async () => {
    telegramLinkServiceMocks.apiFetchMock.mockRejectedValue(new Error('NetworkError'))

    await expect(requestTelegramLink()).rejects.toThrow('ساخت لینک اتصال تلگرام ناموفق بود')
  })
})
