export const MESSAGE_REACTION_CATALOG = [
  '👍',
  '👎',
  '❤️',
  '🔥',
  '😂',
  '😮',
  '😢',
  '🙏',
  '👏',
  '😁',
  '🤔',
  '🤯',
  '😡',
  '🎉',
  '💯',
  '👌',
  '😍',
  '🥰',
  '🤝',
  '🤩',
  '👀',
  '💔',
  '🤣',
  '🫡',
] as const

export const QUICK_MESSAGE_REACTIONS = MESSAGE_REACTION_CATALOG.slice(0, 6)

export const MESSAGE_REACTION_ORDER = new Map<string, number>(
  MESSAGE_REACTION_CATALOG.map((emoji, index) => [emoji, index]),
)

const RECENT_MESSAGE_REACTIONS_STORAGE_KEY = 'chat_recent_message_reactions'
const MAX_RECENT_MESSAGE_REACTIONS = 6

function isSupportedReaction(emoji: string) {
  return MESSAGE_REACTION_ORDER.has(emoji)
}

export function getRecentMessageReactions(): string[] {
  if (typeof window === 'undefined') {
    return []
  }

  try {
    const raw = window.localStorage.getItem(RECENT_MESSAGE_REACTIONS_STORAGE_KEY)
    if (!raw) {
      return []
    }

    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }

    return parsed
      .filter((emoji): emoji is string => typeof emoji === 'string' && isSupportedReaction(emoji))
      .slice(0, MAX_RECENT_MESSAGE_REACTIONS)
  } catch {
    return []
  }
}

export function recordRecentMessageReaction(emoji: string) {
  if (typeof window === 'undefined' || !isSupportedReaction(emoji)) {
    return
  }

  const nextRecent = [emoji, ...getRecentMessageReactions().filter((entry) => entry !== emoji)]
    .slice(0, MAX_RECENT_MESSAGE_REACTIONS)

  try {
    window.localStorage.setItem(RECENT_MESSAGE_REACTIONS_STORAGE_KEY, JSON.stringify(nextRecent))
  } catch {
    // Ignore storage quota / private mode failures.
  }
}

export function buildQuickMessageReactions(availableReactions: string[], currentUserReactionEmoji?: string) {
  const supportedAvailable = availableReactions.filter(isSupportedReaction)
  const allowed = new Set(supportedAvailable)
  const quick: string[] = []

  const pushUnique = (emoji?: string) => {
    if (!emoji || !allowed.has(emoji) || quick.includes(emoji)) {
      return
    }

    quick.push(emoji)
  }

  pushUnique(currentUserReactionEmoji)
  getRecentMessageReactions().forEach(pushUnique)
  QUICK_MESSAGE_REACTIONS.forEach(pushUnique)
  supportedAvailable.forEach(pushUnique)

  return quick.slice(0, 6)
}