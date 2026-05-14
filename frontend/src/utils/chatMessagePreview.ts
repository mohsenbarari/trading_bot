import type { Message } from '../types/chat'

type PreviewMessageType = Message['message_type'] | string | null | undefined

export function parseStructuredMessageContent(content: string | null | undefined): Record<string, any> | null {
    if (!content) return null

    try {
        const parsed = JSON.parse(content)
        return parsed && typeof parsed === 'object' ? parsed : null
    } catch {
        return null
    }
}

export function getMediaCaptionText(
    messageType: PreviewMessageType,
    content: string | null | undefined,
    parsedContent?: Record<string, any> | null,
): string {
    if (messageType !== 'image' && messageType !== 'video') {
        return ''
    }

    const parsed = parsedContent ?? parseStructuredMessageContent(content)
    const caption = parsed?.caption
    return typeof caption === 'string' ? caption.trim() : ''
}

export function getConversationPreviewText(
    messageType: PreviewMessageType,
    content: string | null | undefined,
): string {
    if (!messageType) {
        return ''
    }

    if (messageType === 'image') {
        const caption = getMediaCaptionText(messageType, content)
        return (caption ? `تصویر · ${caption}` : 'تصویر').substring(0, 42)
    }

    if (messageType === 'video') {
        const caption = getMediaCaptionText(messageType, content)
        return (caption ? `ویدئو · ${caption}` : 'ویدئو').substring(0, 42)
    }

    if (messageType === 'voice') return 'پیام صوتی'
    if (messageType === 'sticker') return 'استیکر'
    if (messageType === 'location') return 'موقعیت'
    if (messageType === 'document') return 'فایل'

    return typeof content === 'string' ? content.substring(0, 42) : '...'
}