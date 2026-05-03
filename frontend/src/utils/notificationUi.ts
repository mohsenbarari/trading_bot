import {
    AlertCircle,
    AlertTriangle,
    Bell,
    CheckCircle,
    MessageCircle,
    ShieldAlert,
} from 'lucide-vue-next'
import {
    getNotificationDisplayKind,
    type NotificationToastKind,
} from '../types/notifications'

interface NotificationUiSource {
    kind?: NotificationToastKind
    level?: unknown
    category?: unknown
}

export function getNotificationIconComponent(source: NotificationUiSource) {
    const displayKind = getNotificationDisplayKind(source)

    if (displayKind === 'chat') return MessageCircle
    if (displayKind === 'system') return ShieldAlert
    if (displayKind === 'success') return CheckCircle
    if (displayKind === 'warning') return AlertTriangle
    if (displayKind === 'error') return AlertCircle
    return Bell
}