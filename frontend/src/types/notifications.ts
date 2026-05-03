export const WS_NOTIFICATION_EVENTS = {
    appMessage: 'message',
    chatMessage: 'chat:message',
    sessionLoginRequest: 'session:login_request',
    sessionRevoked: 'session:revoked',
    wsReconnect: 'ws:reconnect',
} as const

export type NotificationCategory = 'system' | 'trade' | 'user'
export type NotificationLevel = 'info' | 'success' | 'warning' | 'error'
export type NotificationToastKind = 'app' | 'chat'
export type NotificationDisplayKind = 'chat' | 'system' | 'success' | 'warning' | 'error' | 'info'

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
    [key: string]: unknown
}

export interface NormalizedAppNotification extends Omit<AppRealtimeNotificationPayload, 'id' | 'title' | 'body' | 'message' | 'content' | 'level' | 'category'> {
    id: number | string
    title: string
    body: string
    message: string
    content: string
    level: NotificationLevel
    category: NotificationCategory
}

export interface ToastInput {
    title: string
    body: string
    route?: string
    kind?: NotificationToastKind
    level?: NotificationLevel
    category?: NotificationCategory
}

export interface ToastNotification extends ToastInput {
    id: number
    kind: NotificationToastKind
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

function normalizeEnumValue(value: unknown): string {
    return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

export function normalizeNotificationId(value: unknown): number | string {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return value
    }

    if (typeof value === 'string') {
        const trimmed = value.trim()
        if (!trimmed) {
            return Date.now() + Math.random()
        }

        const numeric = Number(trimmed)
        return Number.isFinite(numeric) ? numeric : trimmed
    }

    return Date.now() + Math.random()
}

export function normalizeNotificationCategory(value: unknown): NotificationCategory {
    const normalized = normalizeEnumValue(value)
    if (normalized === 'trade' || normalized === 'user') {
        return normalized
    }
    return 'system'
}

export function normalizeNotificationLevel(value: unknown): NotificationLevel {
    const normalized = normalizeEnumValue(value)
    if (normalized === 'success' || normalized === 'warning' || normalized === 'error') {
        return normalized
    }
    return 'info'
}

export function buildNotificationTitle(category: NotificationCategory): string {
    if (category === 'system') return 'پیام مدیریت'
    if (category === 'trade') return 'اعلان معامله'
    if (category === 'user') return 'اعلان کاربری'
    return 'اعلان جدید'
}

export function normalizeAppNotificationPayload(
    notification: AppRealtimeNotificationPayload = {}
): NormalizedAppNotification {
    const category = normalizeNotificationCategory(notification.category)
    const level = normalizeNotificationLevel(notification.level)
    const content = typeof notification.content === 'string' && notification.content.trim()
        ? notification.content
        : ''
    const message = typeof notification.message === 'string' && notification.message.trim()
        ? notification.message
        : ''
    const body = content || message || (typeof notification.body === 'string' ? notification.body : '')

    return {
        ...notification,
        id: normalizeNotificationId(notification.id),
        category,
        level,
        body,
        content: content || body,
        message: message || body,
        title: typeof notification.title === 'string' && notification.title.trim()
            ? notification.title
            : buildNotificationTitle(category),
    }
}

export function createToastNotification(toast: ToastInput): ToastNotification {
    return {
        ...toast,
        id: Date.now() + Math.random(),
        kind: toast.kind || 'app',
    }
}

export function getNotificationDisplayKind(source: {
    kind?: NotificationToastKind
    level?: unknown
    category?: unknown
}): NotificationDisplayKind {
    if (source.kind === 'chat') return 'chat'

    const category = normalizeNotificationCategory(source.category)
    if (category === 'system') return 'system'

    const level = normalizeNotificationLevel(source.level)
    if (level === 'success' || level === 'warning' || level === 'error') {
        return level
    }

    return 'info'
}