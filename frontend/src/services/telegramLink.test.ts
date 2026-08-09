import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  openTelegramAccountLink,
  openTelegramLink,
  requestTelegramLink,
  TELEGRAM_LINK_REQUEST_FAILED_MESSAGE,
  type TelegramLinkResponse,
} from './telegramLink'

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

function mockLocation() {
  const location = { href: 'http://localhost/account' }
  Object.defineProperty(window, 'location', {
    value: location,
    configurable: true,
    writable: true,
  })
  return location
}

describe('telegramLink service', () => {
  beforeEach(() => {
    telegramLinkServiceMocks.apiFetchMock.mockReset()
  })

  it('requests Telegram link tokens without global network retry', async () => {
    telegramLinkServiceMocks.apiFetchMock.mockResolvedValue(
      responseOf({
        telegram_linked: false,
        can_connect_telegram: true,
        bot_username: 'example_bot',
        telegram_url: 'https://t.me/example_bot?start=link_token',
        start_parameter: 'link_token',
      }),
    )

    await expect(requestTelegramLink()).resolves.toMatchObject({
      bot_username: 'example_bot',
      telegram_url: 'https://t.me/example_bot?start=link_token',
      start_parameter: 'link_token',
    })
    expect(telegramLinkServiceMocks.apiFetchMock).toHaveBeenCalledWith(
      '/api/auth/telegram-link-token',
      { method: 'POST', retryNetwork: false },
    )
  })

  it('turns transport failures into a user-facing Telegram link error', async () => {
    telegramLinkServiceMocks.apiFetchMock.mockRejectedValue(new Error('NetworkError'))

    await expect(requestTelegramLink()).rejects.toThrow(TELEGRAM_LINK_REQUEST_FAILED_MESSAGE)
  })

  it('does not expose hostile backend details or accept malformed success receipts', async () => {
    telegramLinkServiceMocks.apiFetchMock
      .mockResolvedValueOnce(
        responseOf({ detail: 'server=iran route=/api/internal/telegram-link' }, false),
      )
      .mockResolvedValueOnce(responseOf({ telegram_linked: false }))

    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await requestTelegramLink()
        throw new Error('Expected Telegram request to fail')
      } catch (error) {
        expect(error).toBeInstanceOf(Error)
        expect((error as Error).message).toBe(TELEGRAM_LINK_REQUEST_FAILED_MESSAGE)
        expect((error as Error).message).not.toContain('server=iran')
        expect((error as Error).message).not.toContain('/api/internal')
      }
    }
  })

  it('opens only canonical HTTPS t.me bot-start links', () => {
    const location = mockLocation()

    expect(openTelegramLink('https://t.me/example_bot?start=token-123')).toBe(true)
    expect(location.href).toBe('https://t.me/example_bot?start=token-123')
  })

  it.each([
    'javascript:alert(1)',
    'http://t.me/example_bot?start=link_token',
    'https://telegram.me/example_bot?start=link_token',
    'https://t.me.evil.example/example_bot?start=link_token',
    'https://user:password@t.me/example_bot?start=link_token',
    'https://t.me/example_bot/extra?start=link_token',
    'https://t.me/example_bot?start=link_token#fragment',
    'https://t.me/example_bot?start=link_token&next=https://evil.example',
    'https://t.me/example_bot?start=one&start=two',
    'https://t.me/example_bot?start=link_token&',
    'https://t.me/example_bot?mode=start',
  ])('rejects a hostile Telegram destination: %s', (destination) => {
    const location = mockLocation()

    expect(openTelegramLink(destination)).toBe(false)
    expect(location.href).toBe('http://localhost/account')
  })

  it('opens account links only when URL, bot and link purpose match the receipt', () => {
    const location = mockLocation()
    const payload: TelegramLinkResponse = {
      telegram_linked: false,
      can_connect_telegram: true,
      bot_username: 'example_bot',
      telegram_url: 'https://t.me/example_bot?start=link_token',
      start_parameter: 'link_token',
    }

    expect(openTelegramAccountLink(payload)).toBe(true)
    expect(location.href).toBe(payload.telegram_url)

    const rejectedPayloads: TelegramLinkResponse[] = [
      { ...payload, telegram_linked: true },
      { ...payload, can_connect_telegram: false },
      { ...payload, bot_username: 'other_bot' },
      { ...payload, start_parameter: 'link_other' },
      {
        ...payload,
        telegram_url: 'https://t.me/example_bot?start=token_without_link_purpose',
        start_parameter: 'token_without_link_purpose',
      },
      { ...payload, telegram_url: 'https://evil.example/example_bot?start=link_token' },
      { ...payload, telegram_url: 'https://t.me/example_bot?start=link_token&next=account' },
    ]

    for (const rejected of rejectedPayloads) {
      location.href = 'http://localhost/account'
      expect(openTelegramAccountLink(rejected)).toBe(false)
      expect(location.href).toBe('http://localhost/account')
    }
  })
})
