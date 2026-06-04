import type { ChatTimelineGroup, ChatTimelineItem, Message } from '../types/chat'
import { resolveMessageMediaDimensions } from './chatMediaDimensions'

export type VirtualDateRow = {
  type: 'date'
  key: string
  group: ChatTimelineGroup
}

export type VirtualMessageRow = {
  type: 'message'
  key: string
  item: ChatTimelineItem
}

export type VirtualTimelineRow = VirtualDateRow | VirtualMessageRow

export function buildVirtualTimelineRows(groups: ChatTimelineGroup[]): VirtualTimelineRow[] {
  const flattened: VirtualTimelineRow[] = []
  for (const group of groups) {
    flattened.push({
      type: 'date',
      key: `date:${group.label}`,
      group,
    })

    for (const item of group.items) {
      flattened.push({
        type: 'message',
        key: `message:${item.id}`,
        item,
      })
    }
  }
  return flattened
}

export function estimateVirtualMessageSize(item: ChatTimelineItem): number {
  if ('messages' in item) {
    const count = item.messages.length
    if (count <= 1) return 270
    if (count <= 3) return 250
    return 330
  }

  const message = item as Message
  if (message.message_type === 'image' || message.message_type === 'video') {
    const dimensions = resolveMessageMediaDimensions(message)
    return Math.min(452, Math.max(210, Math.round(320 / dimensions.aspectRatio) + 64))
  }
  if (message.message_type === 'voice') return 88
  if (message.message_type === 'document') return 112
  if (message.message_type === 'location') return 230

  const textLength = typeof message.content === 'string' ? message.content.length : 0
  const lines = Math.ceil(textLength / 42)
  return Math.min(220, Math.max(58, 44 + lines * 24))
}

export function estimateVirtualTimelineRowSize(
  rows: VirtualTimelineRow[],
  index: number,
  measuredHeights?: ReadonlyMap<string, number>,
): number {
  const row = rows[index]
  if (!row) return 72
  const measured = measuredHeights?.get(row.key)
  if (measured && Number.isFinite(measured) && measured > 0) return measured
  return row.type === 'date' ? 38 : estimateVirtualMessageSize(row.item)
}

export function findVirtualTimelineMessageRowIndex(rows: VirtualTimelineRow[], messageId: number): number {
  return rows.findIndex((row) => {
    if (row.type !== 'message') return false
    if ('messages' in row.item) {
      return row.item.messages.some(message => message.id === messageId)
    }
    return row.item.id === messageId
  })
}
