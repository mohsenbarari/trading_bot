import type {
  ChatAlbumTimelineItem,
  ChatTimelineGroup,
  Message,
} from '../types/chat'

export interface MessengerAlbumMeta {
  albumId: string | null
  albumIndex: number
}

export interface MessengerTimelineCache {
  albumWrappers: Map<string, { signature: string; wrapper: ChatAlbumTimelineItem }>
  groups: Map<string, { group: ChatTimelineGroup }>
}

export interface MessengerSelectionResult {
  selectedMessageIds: number[]
  cleared: boolean
}

export type MessengerRouteQueryValue = string | string[] | null | undefined
export type MessengerRouteQuery = Record<string, MessengerRouteQueryValue>

export function getRouteQueryValue(value: MessengerRouteQueryValue) {
  if (Array.isArray(value)) {
    return value[0] || ''
  }

  return value || ''
}

export function buildMessengerConversationQuery(
  currentQuery: MessengerRouteQuery,
  userId: number | null,
  userName = '',
) {
  const nextQuery: Record<string, string> = {}

  Object.entries(currentQuery).forEach(([key, value]) => {
    if (key === 'user_id' || key === 'user_name') {
      return
    }

    const normalizedValue = getRouteQueryValue(value)
    if (normalizedValue) {
      nextQuery[key] = normalizedValue
    }
  })

  if (userId != null) {
    nextQuery.user_id = String(userId)
  }

  const normalizedName = userName.trim()
  if (normalizedName) {
    nextQuery.user_name = normalizedName
  }

  return nextQuery
}

export function normalizeMessageIds(messageIds: number[]) {
  const seen = new Set<number>()
  const normalized: number[] = []

  messageIds.forEach((messageId) => {
    if (!Number.isFinite(messageId) || seen.has(messageId)) return
    seen.add(messageId)
    normalized.push(messageId)
  })

  return normalized
}

export function sortMessageIdsByMessageOrder(messageIds: number[], messages: Pick<Message, 'id'>[]) {
  const normalized = normalizeMessageIds(messageIds)
  const positionById = new Map<number, number>()

  messages.forEach((message, index) => {
    positionById.set(message.id, index)
  })

  return [...normalized].sort((left, right) => {
    return (positionById.get(left) ?? Number.MAX_SAFE_INTEGER) - (positionById.get(right) ?? Number.MAX_SAFE_INTEGER)
  })
}

export function toggleMessageSelectionBatch(
  currentSelection: number[],
  messageIds: number[],
  messages: Pick<Message, 'id'>[],
): MessengerSelectionResult {
  const normalized = sortMessageIdsByMessageOrder(messageIds, messages)
  if (normalized.length === 0) {
    return { selectedMessageIds: currentSelection, cleared: false }
  }

  const allSelected = normalized.every(messageId => currentSelection.includes(messageId))
  if (allSelected) {
    const selectedMessageIds = currentSelection.filter(messageId => !normalized.includes(messageId))
    return { selectedMessageIds, cleared: selectedMessageIds.length === 0 }
  }

  return {
    selectedMessageIds: sortMessageIdsByMessageOrder([
      ...currentSelection,
      ...normalized,
    ], messages),
    cleared: false,
  }
}

export function getAlbumMeta(msg: Message): MessengerAlbumMeta {
  if (msg.message_type !== 'image' && msg.message_type !== 'video') {
    return { albumId: null, albumIndex: Number.MAX_SAFE_INTEGER }
  }

  try {
    const content = JSON.parse(msg.content)
    const albumId = typeof content.album_id === 'string' && content.album_id.trim()
      ? content.album_id.trim()
      : null
    const albumIndex = typeof content.album_index === 'number' && Number.isFinite(content.album_index)
      ? content.album_index
      : Number.MAX_SAFE_INTEGER

    return { albumId, albumIndex }
  } catch {
    return { albumId: null, albumIndex: Number.MAX_SAFE_INTEGER }
  }
}

export function getAlbumMessagesForMessage(msg: Message, messages: Message[]) {
  const albumMeta = getAlbumMeta(msg)
  if (!albumMeta.albumId) {
    return [msg]
  }

  const albumMessages = messages
    .filter(candidate => {
      if (candidate.message_type !== 'image' && candidate.message_type !== 'video') return false
      if (candidate.reply_to_message || candidate.is_error) return false
      if (candidate.sender_id !== msg.sender_id) return false
      return getAlbumMeta(candidate).albumId === albumMeta.albumId
    })
    .sort((left, right) => {
      const leftMeta = getAlbumMeta(left)
      const rightMeta = getAlbumMeta(right)
      const byIndex = leftMeta.albumIndex - rightMeta.albumIndex
      if (byIndex !== 0) return byIndex

      const byCreatedAt = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
      if (byCreatedAt !== 0) return byCreatedAt

      return left.id - right.id
    })

  return albumMessages.length > 0 ? albumMessages : [msg]
}

export function getContextMenuMessageIds(msg: Message, messages: Message[]) {
  const albumMessages = getAlbumMessagesForMessage(msg, messages)
  return normalizeMessageIds(albumMessages.length > 1 ? albumMessages.map(message => message.id) : [msg.id])
}

export function createMessengerTimelineCache(): MessengerTimelineCache {
  return {
    albumWrappers: new Map(),
    groups: new Map(),
  }
}

export function clearMessengerTimelineCache(cache: MessengerTimelineCache) {
  cache.albumWrappers.clear()
  cache.groups.clear()
}

function buildAlbumSignature(bucket: Message[]) {
  return bucket
    .map(message => `${message.id}:${message.is_deleted ? 1 : 0}:${message.content?.length ?? 0}:${JSON.stringify(message.reactions ?? [])}`)
    .join('|')
}

export function groupMessengerMessages(
  messages: Message[],
  formatDateForSeparator: (dateStr: string) => string,
  cache: MessengerTimelineCache,
): ChatTimelineGroup[] {
  const groups: ChatTimelineGroup[] = []
  if (messages.length === 0) return groups

  const firstMsg = messages[0]
  if (!firstMsg) return groups

  let currentLabel = formatDateForSeparator(firstMsg.created_at)
  let currentGroup: Message[] = [firstMsg]

  for (let i = 1; i < messages.length; i++) {
    const msg = messages[i]
    if (!msg) continue
    const label = formatDateForSeparator(msg.created_at)
    if (label !== currentLabel) {
      groups.push({ label: currentLabel, items: currentGroup })
      currentLabel = label
      currentGroup = [msg]
    } else {
      currentGroup.push(msg)
    }
  }
  groups.push({ label: currentLabel, items: currentGroup })

  groups.forEach(group => {
    const albumMetaByMessageId = new Map<number, MessengerAlbumMeta>()
    const albumBuckets = new Map<string, Message[]>()

    group.items.forEach(item => {
      if ('type' in item && item.type === 'album') return
      const msg = item as Message
      const meta = getAlbumMeta(msg)
      albumMetaByMessageId.set(msg.id, meta)

      if (!meta.albumId) return
      const isMedia = msg.message_type === 'image' || msg.message_type === 'video'
      if (!isMedia || msg.reply_to_message || msg.is_error) return

      const bucketKey = `${msg.sender_id}::${meta.albumId}`
      const bucket = albumBuckets.get(bucketKey)
      if (bucket) {
        bucket.push(msg)
      } else {
        albumBuckets.set(bucketKey, [msg])
      }
    })

    albumBuckets.forEach((bucket) => {
      bucket.sort((left, right) => {
        const leftMeta = albumMetaByMessageId.get(left.id)
        const rightMeta = albumMetaByMessageId.get(right.id)
        const byIndex = (leftMeta?.albumIndex ?? Number.MAX_SAFE_INTEGER) - (rightMeta?.albumIndex ?? Number.MAX_SAFE_INTEGER)
        if (byIndex !== 0) return byIndex
        const byCreatedAt = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
        if (byCreatedAt !== 0) return byCreatedAt
        return left.id - right.id
      })
    })

    const collapsedItems: ChatTimelineGroup['items'] = []
    const consumedAlbumKeys = new Set<string>()

    group.items.forEach(item => {
      if ('type' in item && item.type === 'album') {
        collapsedItems.push(item)
        return
      }

      const msg = item as Message
      const isMedia = msg.message_type === 'image' || msg.message_type === 'video'
      if (!isMedia || msg.reply_to_message || msg.is_error) {
        collapsedItems.push(msg)
        return
      }

      const meta = albumMetaByMessageId.get(msg.id)
      if (!meta?.albumId) {
        collapsedItems.push(msg)
        return
      }

      const bucketKey = `${msg.sender_id}::${meta.albumId}`
      if (consumedAlbumKeys.has(bucketKey)) return
      consumedAlbumKeys.add(bucketKey)

      const bucket = albumBuckets.get(bucketKey) ?? [msg]
      if (bucket.length > 1) {
        const signature = buildAlbumSignature(bucket)
        const cacheKey = `${msg.sender_id}::${meta.albumId}`
        const cached = cache.albumWrappers.get(cacheKey)
        if (cached && cached.signature === signature) {
          collapsedItems.push(cached.wrapper)
        } else {
          const wrapper: ChatAlbumTimelineItem = {
            type: 'album',
            id: `album_${meta.albumId}`,
            sender_id: msg.sender_id,
            messages: bucket,
          }
          cache.albumWrappers.set(cacheKey, { signature, wrapper })
          collapsedItems.push(wrapper)
        }
      } else {
        collapsedItems.push(msg)
      }
    })

    group.items = collapsedItems
  })

  const stableGroups: ChatTimelineGroup[] = []
  const seenLabels = new Set<string>()
  for (const group of groups) {
    const cacheKey = group.label
    const cached = cache.groups.get(cacheKey)
    let reuse = false
    if (cached && cached.group.items.length === group.items.length) {
      reuse = true
      const prevItems = cached.group.items
      const nextItems = group.items
      for (let i = 0; i < nextItems.length; i++) {
        if (prevItems[i] !== nextItems[i]) { reuse = false; break }
      }
    }
    if (reuse && cached) {
      stableGroups.push(cached.group)
    } else {
      const stable = { label: group.label, items: group.items }
      cache.groups.set(cacheKey, { group: stable })
      stableGroups.push(stable)
    }
    seenLabels.add(cacheKey)
  }
  if (cache.groups.size > seenLabels.size) {
    for (const key of cache.groups.keys()) {
      if (!seenLabels.has(key)) cache.groups.delete(key)
    }
  }

  return stableGroups
}