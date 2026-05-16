import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('emojiStickerCatalog', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2024-01-01T00:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('exposes Telegram-style categories and counts emoji graphemes correctly', async () => {
    const catalog = await import('./emojiStickerCatalog')

    expect(catalog.MAX_STICKERS_PER_MESSAGE).toBe(24)
    expect(catalog.TELEGRAM_EMOJI_CATEGORIES[0]?.id).toBe('smileys_people')
    expect(catalog.TELEGRAM_EMOJI_CATEGORIES[0]?.emojis.length).toBeGreaterThan(0)
    expect(catalog.splitTextGraphemes('😂a')).toEqual(['😂', 'a'])
    expect(catalog.countEmojiStickerOccurrences('😂😂 hello')).toBe(2)
    expect(catalog.isEmojiStickerOnlyMessage('😂 😂')).toBe(true)
    expect(catalog.isEmojiStickerOnlyMessage('  ')).toBe(false)
    expect(catalog.isEmojiStickerOnlyMessage('hello 😂')).toBe(false)
  })

  it('records emoji usage, ignores unknown emoji, and builds a frequent category by usage and recency', async () => {
    const catalog = await import('./emojiStickerCatalog')

    catalog.recordEmojiStickerUsage(5, '😂')
    vi.setSystemTime(new Date('2024-01-01T00:00:10Z'))
    catalog.recordEmojiStickerUsage(5, '❤️')
    vi.setSystemTime(new Date('2024-01-01T00:00:20Z'))
    catalog.recordEmojiStickerUsage(5, '😂')
    catalog.recordEmojiStickerUsage(5, 'not-an-emoji')

    const usageState = JSON.parse(localStorage.getItem('chat_emoji_sticker_usage_v1:5') || '{}')
    expect(usageState['😂']).toMatchObject({ count: 2, lastUsedAt: Date.now() })
    expect(usageState['not-an-emoji']).toBeUndefined()

    const frequent = catalog.buildFrequentEmojiCategory(5, 5)
    expect(frequent).toMatchObject({
      id: 'frequent',
      label: 'پراستفاده‌ها',
      icon: '🕘',
    })
    expect(frequent.emojis).toHaveLength(5)
    expect(frequent.emojis[0]?.emoji).toBe('😂')
    expect(frequent.emojis.some((entry) => entry.emoji === '❤️')).toBe(true)
  })

  it('sanitizes malformed usage storage and falls back when Segmenter or window are unavailable', async () => {
    localStorage.setItem(
      'chat_emoji_sticker_usage_v1:9',
      JSON.stringify({
        '😂': { count: 'broken', lastUsedAt: 1 },
        bogus: { count: 4, lastUsedAt: 2 },
      }),
    )

    let catalog = await import('./emojiStickerCatalog')
    const sanitizedFrequent = catalog.buildFrequentEmojiCategory(9, 3)
    expect(sanitizedFrequent.emojis).toHaveLength(3)
    expect(sanitizedFrequent.emojis.every((entry) => entry.emoji !== 'bogus')).toBe(true)

    vi.resetModules()
    Object.defineProperty(Intl, 'Segmenter', {
      configurable: true,
      value: undefined,
    })
    catalog = await import('./emojiStickerCatalog')
    expect(catalog.splitTextGraphemes('ab')).toEqual(['a', 'b'])

    vi.resetModules()
    vi.stubGlobal('window', undefined)
    catalog = await import('./emojiStickerCatalog')
    expect(catalog.buildFrequentEmojiCategory(null, 2).emojis).toHaveLength(2)
    expect(() => catalog.recordEmojiStickerUsage(null, '😂')).not.toThrow()
  })
})