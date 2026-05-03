export const WS_NOTIFICATION_EVENTS = {
    appMessage: 'message',
    chatMessage: 'chat:message',
    sessionLoginRequest: 'session:login_request',
    sessionRevoked: 'session:revoked',
    wsReconnect: 'ws:reconnect',
} as const

export interface AppRealtimeNotificationPayload {
    id?: number | string
    title?: string
    body?: string
    message?: string
    content?: string
    level?: string
    category?: string
    is_read?: boolean
    created_at?: string
}

export interface ChatRealtimeNotificationPayload {
    sender_id?: number | string
    sender_name?: string
    content?: string
    message_type?: string
}

export interface SessionLoginRequestPayload {
    request_id?: string
    device_name?: string
    device_ip?: string
    expires_at?: string
}

export interface BrowserNotificationClickDetail {
    route?: string
}