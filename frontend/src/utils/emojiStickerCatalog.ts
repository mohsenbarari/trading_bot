import emojiGroups from 'unicode-emoji-json/data-by-group.json'
import orderedEmojis from 'unicode-emoji-json/data-ordered-emoji.json'

type RawEmojiEntry = {
  emoji: string
  name: string
  slug: string
  skin_tone_support: boolean
  unicode_version: string
  emoji_version: string
}

type RawEmojiGroup = {
  name: string
  slug: string
  emojis: RawEmojiEntry[]
}

type EmojiUsageRecord = {
  count: number
  lastUsedAt: number
}

type EmojiUsageState = Record<string, EmojiUsageRecord>

export type EmojiStickerEntry = {
  emoji: string
  name: string
  slug: string
}

export type EmojiStickerCategory = {
  id: string
  label: string
  icon: string
  emojis: EmojiStickerEntry[]
}

const USAGE_STORAGE_PREFIX = 'chat_emoji_sticker_usage_v1'

const TELEGRAM_CATEGORY_META = [
  { id: 'smileys_people', label: 'لبخندها و افراد', icon: '😄' },
  { id: 'animals_nature', label: 'حیوانات و طبیعت', icon: '🐻' },
  { id: 'food_drink', label: 'غذا و نوشیدنی', icon: '🍔' },
  { id: 'travel_places', label: 'سفر و مکان‌ها', icon: '🚗' },
  { id: 'activities', label: 'فعالیت‌ها', icon: '⚽' },
  { id: 'objects', label: 'اشیا', icon: '💡' },
  { id: 'symbols', label: 'نمادها', icon: '💯' },
  { id: 'flags', label: 'پرچم‌ها', icon: '🚩' },
] as const

const TELEGRAM_CATEGORY_LOOKUP: Record<string, (typeof TELEGRAM_CATEGORY_META)[number]['id'] | null> = {
  'Smileys & Emotion': 'smileys_people',
  'People & Body': 'smileys_people',
  'Animals & Nature': 'animals_nature',
  'Food & Drink': 'food_drink',
  'Travel & Places': 'travel_places',
  Activities: 'activities',
  Objects: 'objects',
  Symbols: 'symbols',
  Flags: 'flags',
  Component: null,
}

const DEFAULT_FREQUENT_EMOJIS = [
  '😂', '❤️', '😍', '🤣', '😊', '🙏', '💕', '😭', '😘', '👍',
  '🔥', '🥰', '😅', '😁', '😎', '🤔', '😉', '💔', '🙌', '👏',
  '🤝', '👌', '🥺', '😔', '😢', '🤗', '🤷', '🎉', '✨', '💯',
  '🌹', '🤍', '♥️', '☀️', '😴', '😡', '🙈', '👀', '💪', '🤲',
]

const rawGroups = emojiGroups as RawEmojiGroup[]
const orderedEmojiList = orderedEmojis as string[]
const orderedIndex = new Map<string, number>(
  orderedEmojiList.map((emoji, index) => [emoji, index])
)

const categoryBuckets = new Map<string, EmojiStickerEntry[]>()

for (const category of TELEGRAM_CATEGORY_META) {
  categoryBuckets.set(category.id, [])
}

for (const group of rawGroups) {
  const categoryId = TELEGRAM_CATEGORY_LOOKUP[group.name]
  if (!categoryId) continue

  const bucket = categoryBuckets.get(categoryId)
  if (!bucket) continue

  for (const entry of group.emojis) {
    bucket.push({
      emoji: entry.emoji,
      name: entry.name,
      slug: entry.slug,
    })
  }
}

function compareEmojiOrder(left: EmojiStickerEntry, right: EmojiStickerEntry) {
  return (orderedIndex.get(left.emoji) ?? Number.MAX_SAFE_INTEGER) - (orderedIndex.get(right.emoji) ?? Number.MAX_SAFE_INTEGER)
}

export const TELEGRAM_EMOJI_CATEGORIES: EmojiStickerCategory[] = TELEGRAM_CATEGORY_META.map((category) => ({
  ...category,
  emojis: [...(categoryBuckets.get(category.id) ?? [])].sort(compareEmojiOrder),
}))

const EMOJI_ENTRY_BY_CHAR = new Map<string, EmojiStickerEntry>()

for (const category of TELEGRAM_EMOJI_CATEGORIES) {
  for (const entry of category.emojis) {
    EMOJI_ENTRY_BY_CHAR.set(entry.emoji, entry)
  }
}

const fallbackEmojiEntries = orderedEmojiList
  .map((emoji) => EMOJI_ENTRY_BY_CHAR.get(emoji))
  .filter((entry): entry is EmojiStickerEntry => Boolean(entry))

type GraphemeSegment = {
  segment: string
}

type GraphemeSegmenterLike = {
  segment: (input: string) => Iterable<GraphemeSegment>
}

const intlWithSegmenter = globalThis.Intl as typeof Intl & {
  Segmenter?: new (
    locales?: string | string[],
    options?: { granularity?: 'grapheme' | 'word' | 'sentence' }
  ) => GraphemeSegmenterLike
}

const graphemeSegmenter: GraphemeSegmenterLike | null = intlWithSegmenter?.Segmenter
  ? new intlWithSegmenter.Segmenter(undefined, { granularity: 'grapheme' })
  : null

export const MAX_STICKERS_PER_MESSAGE = 24

export function splitTextGraphemes(text: string) {
  if (!text) return []

  if (graphemeSegmenter) {
    return Array.from(graphemeSegmenter.segment(text), (entry: GraphemeSegment) => entry.segment)
  }

  return Array.from(text)
}

export function countEmojiStickerOccurrences(text: string) {
  let count = 0

  for (const segment of splitTextGraphemes(text)) {
    if (EMOJI_ENTRY_BY_CHAR.has(segment)) {
      count += 1
    }
  }

  return count
}

export function isEmojiStickerOnlyMessage(text: string) {
  const meaningfulSegments = splitTextGraphemes(text).filter((segment) => segment.trim().length > 0)
  if (meaningfulSegments.length === 0) {
    return false
  }

  return meaningfulSegments.every((segment) => EMOJI_ENTRY_BY_CHAR.has(segment))
}

function getUsageStorageKey(userId: number | null) {
  return `${USAGE_STORAGE_PREFIX}:${userId ?? 'guest'}`
}

function isUsageRecord(value: unknown): value is EmojiUsageRecord {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<EmojiUsageRecord>
  return typeof candidate.count === 'number' && typeof candidate.lastUsedAt === 'number'
}

function readUsageState(userId: number | null): EmojiUsageState {
  if (typeof window === 'undefined') return {}

  try {
    const raw = window.localStorage.getItem(getUsageStorageKey(userId))
    if (!raw) return {}

    const parsed = JSON.parse(raw) as Record<string, unknown>
    const sanitized: EmojiUsageState = {}

    for (const [emoji, value] of Object.entries(parsed)) {
      if (!EMOJI_ENTRY_BY_CHAR.has(emoji) || !isUsageRecord(value)) continue
      sanitized[emoji] = value
    }

    return sanitized
  } catch {
    return {}
  }
}

function writeUsageState(userId: number | null, state: EmojiUsageState) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(getUsageStorageKey(userId), JSON.stringify(state))
}

export function recordEmojiStickerUsage(userId: number | null, emoji: string) {
  if (!EMOJI_ENTRY_BY_CHAR.has(emoji)) return

  const state = readUsageState(userId)
  const previous = state[emoji]
  state[emoji] = {
    count: (previous?.count ?? 0) + 1,
    lastUsedAt: Date.now(),
  }

  writeUsageState(userId, state)
}

export function buildFrequentEmojiCategory(userId: number | null, limit = 56): EmojiStickerCategory {
  const usageState = readUsageState(userId)
  const usedEntries = Object.entries(usageState)
    .map(([emoji, usage]) => {
      const entry = EMOJI_ENTRY_BY_CHAR.get(emoji)
      if (!entry) return null
      return { entry, usage }
    })
    .filter((value): value is { entry: EmojiStickerEntry; usage: EmojiUsageRecord } => Boolean(value))
    .sort((left, right) => {
      if (right.usage.count !== left.usage.count) {
        return right.usage.count - left.usage.count
      }
      if (right.usage.lastUsedAt !== left.usage.lastUsedAt) {
        return right.usage.lastUsedAt - left.usage.lastUsedAt
      }
      return compareEmojiOrder(left.entry, right.entry)
    })
    .map((value) => value.entry)

  const seen = new Set<string>()
  const merged: EmojiStickerEntry[] = []

  const pushUnique = (entry: EmojiStickerEntry | undefined) => {
    if (!entry || seen.has(entry.emoji)) return
    seen.add(entry.emoji)
    merged.push(entry)
  }

  for (const entry of usedEntries) {
    pushUnique(entry)
  }

  for (const emoji of DEFAULT_FREQUENT_EMOJIS) {
    pushUnique(EMOJI_ENTRY_BY_CHAR.get(emoji))
  }

  for (const entry of fallbackEmojiEntries) {
    if (merged.length >= limit) break
    pushUnique(entry)
  }

  return {
    id: 'frequent',
    label: 'پراستفاده‌ها',
    icon: '🕘',
    emojis: merged.slice(0, limit),
  }
}