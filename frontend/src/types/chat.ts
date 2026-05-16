export type ChatKind = 'direct' | 'channel' | 'group'

export interface Conversation {
    id: number
    chat_id?: number | null
    other_user_id: number
    other_user_name: string
    avatar_file_id?: string | null
    profile_user_id?: number | null
    profile_account_name?: string | null
    resolved_from_accountant_id?: number | null
    highlight_accountant_user_id?: number | null
    highlight_accountant_relation_display_name?: string | null
    other_user_is_deleted?: boolean
    last_message_content: string | null
    last_message_type: string | null
    last_message_at: string | null
    unread_count: number
    other_user_last_seen_at?: string | null
    room_kind?: ChatKind
    can_send?: boolean
    member_role?: 'admin' | 'member' | null
    member_count?: number | null
    max_members?: number | null
    is_system?: boolean
    is_mandatory?: boolean
    is_muted?: boolean
    is_pinned?: boolean
    pinned_at?: string | null
    pin_order?: number | null
}

export type ChatListItem = Conversation

export interface MessageReaction {
    emoji: string
    user_id: number
}

export interface RecoveryAction {
    recovery_id: string
    status: string
    prompt_type: 'initial_request' | 'identity_submitted'
    expires_at?: string | null
    can_approve?: boolean
    can_reject?: boolean
    can_request_identity?: boolean
    current_action_message_id?: number | null
    user_id?: number | null
    user_name?: string | null
}

export interface Message {
    id: number
    sender_id: number
    receiver_id: number
    content: string
    message_type: 'text' | 'image' | 'video' | 'voice' | 'sticker' | 'location' | 'document'
    is_read: boolean
    is_sending?: boolean
    upload_handoff_pending?: boolean
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
    forwarded_from_id?: number | null
    forwarded_from_name?: string | null
    forwarded_from_profile_user_id?: number | null
    forwarded_from_profile_account_name?: string | null
    forwarded_from_resolved_from_accountant_id?: number | null
    forwarded_from_highlight_accountant_user_id?: number | null
    forwarded_from_highlight_accountant_relation_display_name?: string | null
    sender_name?: string | null
    sender_profile_user_id?: number | null
    sender_profile_account_name?: string | null
    sender_resolved_from_accountant_id?: number | null
    sender_highlight_accountant_user_id?: number | null
    sender_highlight_accountant_relation_display_name?: string | null
    reactions?: MessageReaction[]
    recovery_action?: RecoveryAction | null
    reply_to_message?: {
        id: number
        sender_id: number
        content: string
        message_type: string
    }
}

export interface PinnedMessageState {
    chat_id?: number | null
    room_kind: ChatKind
    pinned_at?: string | null
    pinned_by_user_id?: number | null
    message?: Message | null
}

export interface ChatForwardTarget {
    kind: 'user' | 'channel' | 'group'
    id: number
    title: string
    subtitle?: string | null
}
