import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { playNotificationSound } from '../utils/audio'


function normalizeEnumValue(value: unknown): string {
    return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

function normalizeNotificationId(value: unknown): number | string {
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

function buildNotificationTitle(category: string): string {
    if (category === 'system') return 'پیام مدیریت'
    if (category === 'trade') return 'اعلان معامله'
    if (category === 'user') return 'اعلان کاربری'
    return 'اعلان جدید'
}

function normalizeNotificationPayload(notification: any) {
    const category = normalizeEnumValue(notification?.category) || 'system'
    const level = normalizeEnumValue(notification?.level) || 'info'
    const body = notification?.content || notification?.message || notification?.body || ''

    return {
        ...notification,
        id: normalizeNotificationId(notification?.id),
        category,
        level,
        body,
        content: notification?.content || body,
        message: notification?.message || body,
        title: notification?.title || buildNotificationTitle(category),
    }
}


export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const unreadChatUserIds = ref<number[]>([])
    const appNotifications = ref<any[]>([])
    const activeToasts = ref<any[]>([])
    const isLoadingHistory = ref(false)

    const syncUnreadChatIds = (conversations: Array<{ user_id?: unknown }> = [], fallbackCount = 0) => {
        const nextIds = Array.from(new Set(
            conversations
                .map((conversation) => Number(conversation?.user_id))
                .filter((userId) => Number.isFinite(userId) && userId > 0)
        ))

        unreadChatUserIds.value = nextIds
        chatUnreadCount.value = nextIds.length > 0 || fallbackCount === 0
            ? nextIds.length
            : fallbackCount
    }


    const fetchInitialCounts = async () => {
        const token = localStorage.getItem('auth_token')
        if (!token) return

        try {
            // Get unread counts from chat poll endpoint
            const response = await apiFetch('/api/chat/poll')
            if (response.ok) {
                const data = await response.json()
                syncUnreadChatIds(data.conversations_with_unread || [], data.unread_chats_count || 0)
            }
        } catch (error) {
            console.error('Failed to fetch initial notification counts:', error)
        }
    }

    const setChatUnreadCount = (count: number) => {
        chatUnreadCount.value = Math.max(0, Number(count) || 0)
        if (chatUnreadCount.value === 0) {
            unreadChatUserIds.value = []
        }
    }

    const incrementChatUnread = (userId?: number | null) => {
        if (!Number.isFinite(userId) || !userId || userId <= 0) {
            void fetchInitialCounts()
            return
        }

        if (!unreadChatUserIds.value.includes(userId)) {
            unreadChatUserIds.value = [...unreadChatUserIds.value, userId]
            chatUnreadCount.value = unreadChatUserIds.value.length
        }
    }

    const markChatAsRead = (userId?: number | null) => {
        if (!Number.isFinite(userId) || !userId || userId <= 0) return
        unreadChatUserIds.value = unreadChatUserIds.value.filter((id) => id !== userId)
        chatUnreadCount.value = unreadChatUserIds.value.length
    }

    const addAppNotification = (notification: any) => {
        const normalized = normalizeNotificationPayload(notification)
        
        if (!appNotifications.value.some(n => n.id === normalized.id)) {
            appNotifications.value.unshift(normalized)
            // Keep only latest 100 for memory
            if (appNotifications.value.length > 100) {
                appNotifications.value.pop()
            }
        }

        return normalized
    }

    const fetchHistory = async () => {
        isLoadingHistory.value = true
        try {
            const res = await apiFetch('/api/notifications/')
            if (res.ok) {
                const data = await res.json()
                appNotifications.value = data.map((n: any) => normalizeNotificationPayload(n))
            }
        } catch (e) {
            console.error('Failed to fetch notifications history:', e)
        } finally {
            isLoadingHistory.value = false
        }
    }

    const markAllAsRead = async () => {
        try {
             await apiFetch('/api/notifications/mark-all-read', { method: 'POST' })
             appNotifications.value.forEach(n => n.is_read = true)
        } catch (e) {
             console.error('Failed to mark all as read:', e)
        }
    }

    const clearAllNotifications = async () => {
        const originalList = [...appNotifications.value]
        appNotifications.value = []

        try {
            const res = await apiFetch('/api/notifications/', { method: 'DELETE' })
            if (!res.ok) throw new Error()
        } catch (e) {
            appNotifications.value = originalList
            console.error('Clear all notifications failed:', e)
        }
    }

    const deleteNotification = async (id: number) => {
        // Optimistic delete
        const originalList = [...appNotifications.value]
        appNotifications.value = appNotifications.value.filter(n => n.id !== id)
        
        try {
            const res = await apiFetch(`/api/notifications/${id}`, { method: 'DELETE' })
            if (!res.ok) throw new Error()
        } catch (e) {
            appNotifications.value = originalList
            console.error('Delete failed:', e)
        }
    }

    const addToast = (title: string, body: string, route?: string) => {
        const id = Date.now() + Math.random()
        activeToasts.value.push({ id, title, body, route })
        
        // Play notification sound
        playNotificationSound()

        // Auto remove after 5 seconds
        setTimeout(() => {
            removeToast(id)
        }, 5000)
    }

    const removeToast = (id: number) => {
        activeToasts.value = activeToasts.value.filter(t => t.id !== id)
    }

    return {
        chatUnreadCount,
        appNotifications,
        activeToasts,
        fetchInitialCounts,
        setChatUnreadCount,
        incrementChatUnread,
        markChatAsRead,
        addAppNotification,
        addToast,
        removeToast,
        isLoadingHistory,
        fetchHistory,
        markAllAsRead,
        clearAllNotifications,
        deleteNotification
    }
})
