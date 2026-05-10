export type ChatKind = 'direct' | 'channel' | 'group'

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
    room_kind?: ChatKind
    chat_id?: number | null
    can_send?: boolean
    member_role?: 'admin' | 'member' | null
    member_count?: number | null
    max_members?: number | null
    is_system?: boolean
    is_mandatory?: boolean
    is_muted?: boolean
    is_pinned?: boolean
    pinned_at?: string | null
}

export type ChatListItem = Conversation

export interface MessageReaction {
    emoji: string
    user_id: number
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
    reactions?: MessageReaction[]
    reply_to_message?: {
        id: number
        sender_id: number
        content: string
        message_type: string
    }
}

export interface ChatForwardTarget {
    kind: 'user' | 'channel' | 'group'
    id: number
    title: string
    subtitle?: string | null
}
