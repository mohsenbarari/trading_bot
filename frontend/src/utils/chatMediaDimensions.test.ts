import { describe, expect, it } from 'vitest'

import {
  clampMediaAspectRatio,
  normalizeTimelineMediaDimensions,
  resolveMessageMediaDimensions,
} from './chatMediaDimensions'
import type { ChatTimelineGroup, Message } from '../types/chat'

function message(overrides: Partial<Message>): Message {
  return {
    id: 1,
    sender_id: 1,
    receiver_id: 2,
    content: '',
    message_type: 'image',
    is_read: false,
    created_at: '2026-06-04T00:00:00Z',
    ...overrides,
  } as Message
}

describe('chatMediaDimensions', () => {
  it('prefers additive message media fields over legacy JSON content', () => {
    const dimensions = resolveMessageMediaDimensions(message({
      media_width: 1920,
      media_height: 1080,
      content: JSON.stringify({ width: 640, height: 480 }),
    }))

    expect(dimensions).toMatchObject({
      width: 1920,
      height: 1080,
      aspectRatioCss: '1920 / 1080',
      source: 'message',
    })
    expect(dimensions.aspectRatio).toBeCloseTo(16 / 9)
  })

  it('backfills from legacy JSON width and height', () => {
    const dimensions = resolveMessageMediaDimensions(message({
      content: JSON.stringify({ file_id: 'image-1', width: 800, height: 600 }),
    }))

    expect(dimensions).toMatchObject({
      width: 800,
      height: 600,
      aspectRatioCss: '800 / 600',
      source: 'content',
    })
  })

  it('uses a stable fallback ratio when dimensions are absent or invalid', () => {
    const dimensions = resolveMessageMediaDimensions(message({
      content: JSON.stringify({ file_id: 'image-1', width: 0, height: 0 }),
    }))

    expect(dimensions).toMatchObject({
      width: null,
      height: null,
      aspectRatio: 4 / 3,
      aspectRatioCss: '4 / 3',
      source: 'fallback',
    })
  })

  it('clamps extreme ratios so virtual row estimates remain bounded', () => {
    expect(clampMediaAspectRatio(99)).toBe(2.4)
    expect(clampMediaAspectRatio(0.01)).toBe(0.42)
    expect(clampMediaAspectRatio('bad')).toBe(4 / 3)
  })

  it('normalizes image/video messages inside timeline groups without changing text rows', () => {
    const groups: ChatTimelineGroup[] = [{
      label: 'امروز',
      items: [
        message({ id: 1, content: JSON.stringify({ width: 500, height: 250 }) }),
        message({ id: 2, message_type: 'text', content: 'سلام' }),
        {
          type: 'album',
          id: 'album-1',
          sender_id: 1,
          messages: [
            message({ id: 3, message_type: 'video', content: JSON.stringify({ width: 1280, height: 720 }) }),
          ],
        },
      ],
    }]

    const normalized = normalizeTimelineMediaDimensions(groups)

    expect((normalized[0].items[0] as Message).media_aspect_ratio).toBe(2)
    expect((normalized[0].items[1] as Message).media_aspect_ratio).toBeUndefined()
    expect((normalized[0].items[2] as any).messages[0].media_aspect_ratio).toBeCloseTo(16 / 9)
  })
})
