export interface Conversation {
    id: number
    other_user_id: number
    other_user_name: string
    other_user_is_deleted?: boolean
    last_message_content: string | null
    last_message_type: string | null
    last_message_at: string | null
    unread_count: number
    other_user_last_seen_at?: string | null
}

export interface Message {
    id: number
    sender_id: number
    receiver_id: number
    content: string
    message_type: 'text' | 'image' | 'video' | 'voice' | 'sticker' | 'location' | 'document'
    is_read: boolean
    is_sending?: boolean
    upload_progress?: number
    upload_loaded?: number
    upload_total?: number
    download_progress?: number
    is_downloading?: boolean
    local_blob_url?: string
    is_error?: boolean
    is_deleted?: boolean
    updated_at?: string
    created_at: string
    reply_to_message?: {
        id: number
        sender_id: number
        content: string
        message_type: string
    }
}

export interface StickerPack {
    id: string
    name: string
    stickers: string[]
}
