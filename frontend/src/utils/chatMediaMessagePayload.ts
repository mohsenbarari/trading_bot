export type ChatMediaPayloadMessageType = 'image' | 'video' | 'voice' | 'document'
export type ChatMediaPayloadPhase = 'preview' | 'final'

export type ChatMediaMessagePayloadInput = {
    phase: ChatMediaPayloadPhase
    msgType: ChatMediaPayloadMessageType
    fileId?: string | null
    thumbnail?: string
    serverThumbnail?: string
    width?: number
    height?: number
    durationMs?: number
    albumId?: string | null
    albumIndex?: number
    caption?: string
    fileName?: string
    mimeType?: string
    fileSize?: number
}

function isPositiveNumber(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function isNonNegativeNumber(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function normalizeText(value: unknown): string {
    return typeof value === 'string' ? value.trim() : ''
}

function normalizeAlbumIndex(value: unknown): number {
    if (!isNonNegativeNumber(value)) {
        return 0
    }

    return Math.max(0, Math.trunc(value))
}

export function buildChatMediaMessagePayload(input: ChatMediaMessagePayloadInput): Record<string, unknown> {
    const payload: Record<string, unknown> = {}
    const normalizedFileId = normalizeText(input.fileId)

    if (input.phase === 'final' && normalizedFileId) {
        payload.file_id = normalizedFileId
    } else {
        payload.placeholder = true
    }

    if (input.msgType === 'document') {
        payload.file_name = input.fileName || 'file'
        payload.mime_type = normalizeText(input.mimeType) || 'application/octet-stream'
        if (isNonNegativeNumber(input.fileSize)) {
            payload.size = input.fileSize
        }
        return payload
    }

    if (input.msgType === 'image' || input.msgType === 'video') {
        const hasLocalThumbnail = typeof input.thumbnail === 'string'
        const hasServerThumbnail = typeof input.serverThumbnail === 'string'
        if (hasLocalThumbnail || hasServerThumbnail) {
            const normalizedLocalThumbnail = normalizeText(input.thumbnail)
            const normalizedServerThumbnail = normalizeText(input.serverThumbnail)
            payload.thumbnail = input.phase === 'final'
                ? (normalizedServerThumbnail || normalizedLocalThumbnail || (hasLocalThumbnail ? '' : ''))
                : (normalizedLocalThumbnail || (hasLocalThumbnail ? '' : ''))
        }

        if (isPositiveNumber(input.width) && isPositiveNumber(input.height)) {
            payload.width = input.width
            payload.height = input.height
        }

        const normalizedAlbumId = normalizeText(input.albumId)
        if (normalizedAlbumId) {
            payload.album_id = normalizedAlbumId
            payload.album_index = normalizeAlbumIndex(input.albumIndex)
        }

        const normalizedCaption = normalizeText(input.caption)
        if (normalizedCaption) {
            payload.caption = normalizedCaption
        }
    }

    if ((input.msgType === 'video' || input.msgType === 'voice') && isNonNegativeNumber(input.durationMs)) {
        payload.durationMs = input.durationMs
    }

    return payload
}

export function serializeChatMediaMessagePayload(input: ChatMediaMessagePayloadInput): string {
    return JSON.stringify(buildChatMediaMessagePayload(input))
}