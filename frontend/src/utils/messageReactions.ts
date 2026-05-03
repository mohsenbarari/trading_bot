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