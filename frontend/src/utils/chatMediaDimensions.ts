import type { ChatTimelineGroup, ChatTimelineItem, Message } from '../types/chat'
import { parseStructuredMessageContent } from './chatMessagePreview'

export type MediaDimensionContract = {
  width: number | null
  height: number | null
  aspectRatio: number
  aspectRatioCss: string
  source: 'message' | 'content' | 'fallback'
}

const DEFAULT_MEDIA_RATIO = 4 / 3
const MIN_MEDIA_RATIO = 0.42
const MAX_MEDIA_RATIO = 2.4

function toPositiveNumber(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null
}

export function clampMediaAspectRatio(value: unknown): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return DEFAULT_MEDIA_RATIO
  }
  return Math.min(MAX_MEDIA_RATIO, Math.max(MIN_MEDIA_RATIO, numeric))
}

export function resolveMessageMediaDimensions(
  message: Pick<Message, 'message_type' | 'content' | 'media_width' | 'media_height' | 'media_aspect_ratio'>,
  parsedContent?: Record<string, unknown> | null,
): MediaDimensionContract {
  const explicitWidth = toPositiveNumber(message.media_width)
  const explicitHeight = toPositiveNumber(message.media_height)
  if (explicitWidth && explicitHeight) {
    const ratio = clampMediaAspectRatio(explicitWidth / explicitHeight)
    return {
      width: explicitWidth,
      height: explicitHeight,
      aspectRatio: ratio,
      aspectRatioCss: `${explicitWidth} / ${explicitHeight}`,
      source: 'message',
    }
  }

  const explicitRatio = toPositiveNumber(message.media_aspect_ratio)
  if (explicitRatio) {
    const ratio = clampMediaAspectRatio(explicitRatio)
    return {
      width: null,
      height: null,
      aspectRatio: ratio,
      aspectRatioCss: `${ratio}`,
      source: 'message',
    }
  }

  const parsed = parsedContent ?? parseStructuredMessageContent(message.content)
  const contentWidth = toPositiveNumber(parsed?.width)
  const contentHeight = toPositiveNumber(parsed?.height)
  if (contentWidth && contentHeight) {
    const ratio = clampMediaAspectRatio(contentWidth / contentHeight)
    return {
      width: contentWidth,
      height: contentHeight,
      aspectRatio: ratio,
      aspectRatioCss: `${contentWidth} / ${contentHeight}`,
      source: 'content',
    }
  }

  const contentRatio = toPositiveNumber(parsed?.aspect_ratio ?? parsed?.aspectRatio)
  if (contentRatio) {
    const ratio = clampMediaAspectRatio(contentRatio)
    return {
      width: null,
      height: null,
      aspectRatio: ratio,
      aspectRatioCss: `${ratio}`,
      source: 'content',
    }
  }

  return {
    width: null,
    height: null,
    aspectRatio: DEFAULT_MEDIA_RATIO,
    aspectRatioCss: '4 / 3',
    source: 'fallback',
  }
}

export function normalizeMessageMediaDimensions(message: Message): Message {
  if (message.message_type !== 'image' && message.message_type !== 'video') {
    return message
  }

  const dimensions = resolveMessageMediaDimensions(message)
  return {
    ...message,
    media_width: dimensions.width,
    media_height: dimensions.height,
    media_aspect_ratio: dimensions.aspectRatio,
  }
}

export function normalizeTimelineMediaDimensions(groups: ChatTimelineGroup[]): ChatTimelineGroup[] {
  return groups.map(group => ({
    ...group,
    items: group.items.map((item: ChatTimelineItem) => {
      if ('messages' in item) {
        return {
          ...item,
          messages: item.messages.map(normalizeMessageMediaDimensions),
        }
      }
      return normalizeMessageMediaDimensions(item)
    }),
  }))
}
