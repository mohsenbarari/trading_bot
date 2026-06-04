import { describe, expect, it } from 'vitest'

import {
  buildVirtualTimelineRows,
  estimateVirtualTimelineRowSize,
  findVirtualTimelineMessageRowIndex,
} from './chatVirtualTimeline'
import type { ChatTimelineGroup, Message } from '../types/chat'

function message(id: number, overrides: Partial<Message> = {}): Message {
  return {
    id,
    sender_id: 1,
    receiver_id: 2,
    content: 'سلام',
    message_type: 'text',
    is_read: false,
    created_at: '2026-06-04T00:00:00Z',
    ...overrides,
  } as Message
}

describe('chatVirtualTimeline', () => {
  it('flattens grouped timeline rows with stable date and message keys', () => {
    const rows = buildVirtualTimelineRows([
      {
        label: 'امروز',
        items: [message(1), message(2)],
      },
    ])

    expect(rows.map(row => row.key)).toEqual(['date:امروز', 'message:1', 'message:2'])
    expect(rows[0].type).toBe('date')
    expect(rows[1].type).toBe('message')
  })

  it('estimates known and fallback media rows using reserved aspect ratios', () => {
    const groups: ChatTimelineGroup[] = [{
      label: 'امروز',
      items: [
        message(1, { message_type: 'image', content: JSON.stringify({ width: 1920, height: 1080 }) }),
        message(2, { message_type: 'video', content: JSON.stringify({ file_id: 'old-video' }) }),
      ],
    }]
    const rows = buildVirtualTimelineRows(groups)

    expect(estimateVirtualTimelineRowSize(rows, 1)).toBeLessThan(260)
    expect(estimateVirtualTimelineRowSize(rows, 2)).toBe(304)
  })

  it('uses measured row heights before estimates to reduce layout drift', () => {
    const rows = buildVirtualTimelineRows([{ label: 'امروز', items: [message(1, { content: 'متن طولانی' })] }])
    const measured = new Map<string, number>([['message:1', 149]])

    expect(estimateVirtualTimelineRowSize(rows, 1, measured)).toBe(149)
  })

  it('finds direct and album child message rows for jump requests', () => {
    const rows = buildVirtualTimelineRows([
      {
        label: 'امروز',
        items: [
          message(1),
          {
            type: 'album',
            id: 'album-a',
            sender_id: 1,
            messages: [message(10), message(11)],
          },
        ],
      },
    ])

    expect(findVirtualTimelineMessageRowIndex(rows, 1)).toBe(1)
    expect(findVirtualTimelineMessageRowIndex(rows, 11)).toBe(2)
    expect(findVirtualTimelineMessageRowIndex(rows, 404)).toBe(-1)
  })
})
