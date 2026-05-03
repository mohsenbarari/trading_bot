import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { playNotificationSound } from '../utils/audio'
import {
    createToastNotification,
    normalizeAppNotificationPayload,
    type AppRealtimeNotificationPayload,
    type NormalizedAppNotification,
    type ToastInput,
    type ToastNotification,
} from '../types/notifications'


export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const unreadChatUserIds = ref<number[]>([])
    const appNotifications = ref<NormalizedAppNotification[]>([])
    const activeToasts = ref<ToastNotification[]>([])
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

    const addAppNotification = (notification: AppRealtimeNotificationPayload) => {
        const normalized = normalizeAppNotificationPayload(notification)
        
        if (!appNotifications.value.some((existingNotification) => existingNotification.id === normalized.id)) {
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
                appNotifications.value = data.map((notification: AppRealtimeNotificationPayload) =>
                    normalizeAppNotificationPayload(notification)
                )
            }
        } catch (e) {
            console.error('Failed to fetch notifications history:', e)
        } finally {
            isLoadingHistory.value = false
        }
    }

    const openNotificationCenter = async () => {
        await fetchHistory()
        await markAllAsRead()
    }

    const markAllAsRead = async () => {
        try {
             await apiFetch('/api/notifications/mark-all-read', { method: 'POST' })
             appNotifications.value.forEach((notification) => {
                 notification.is_read = true
             })
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

    const deleteNotification = async (id: number | string) => {
        // Optimistic delete
        const originalList = [...appNotifications.value]
        appNotifications.value = appNotifications.value.filter((notification) => notification.id !== id)
        
        try {
            const res = await apiFetch(`/api/notifications/${id}`, { method: 'DELETE' })
            if (!res.ok) throw new Error()
        } catch (e) {
            appNotifications.value = originalList
            console.error('Delete failed:', e)
        }
    }

    const addToast = (toast: ToastInput) => {
        const nextToast = createToastNotification(toast)
        activeToasts.value.push(nextToast)
        
        // Play notification sound
        playNotificationSound()

        // Auto remove after 5 seconds
        setTimeout(() => {
            removeToast(nextToast.id)
        }, 5000)
    }

    const removeToast = (id: number) => {
        activeToasts.value = activeToasts.value.filter((toast) => toast.id !== id)
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
        openNotificationCenter,
        markAllAsRead,
        clearAllNotifications,
        deleteNotification
    }
})
