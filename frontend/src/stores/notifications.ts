import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { playNotificationSound } from '../utils/audio'
import {
    createToastNotification,
    normalizeAppNotificationPayload,
    normalizeNotificationId,
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
    const MAX_IN_MEMORY_NOTIFICATIONS = 100
    const TOAST_LIFETIME_MS = 5000

    const trimNotificationList = (notifications: NormalizedAppNotification[]) =>
        notifications.slice(0, MAX_IN_MEMORY_NOTIFICATIONS)

    const withClientReceivedAt = (
        notification: AppRealtimeNotificationPayload,
        fallbackTimestamp = Date.now(),
        existingNotification?: NormalizedAppNotification
    ): NormalizedAppNotification => ({
        ...normalizeAppNotificationPayload(notification),
        client_received_at:
            existingNotification?.client_received_at
            ?? (typeof notification.client_received_at === 'number' ? notification.client_received_at : fallbackTimestamp),
    })

    const replaceHistoryPreservingConcurrentRealtime = (
        incomingNotifications: NormalizedAppNotification[],
        fetchStartedAt: number
    ) => {
        const incomingIds = new Set(incomingNotifications.map((notification) => notification.id))
        const concurrentRealtimeNotifications = appNotifications.value.filter((notification) =>
            !incomingIds.has(notification.id)
            && typeof notification.client_received_at === 'number'
            && notification.client_received_at > fetchStartedAt
        )

        appNotifications.value = trimNotificationList([
            ...concurrentRealtimeNotifications,
            ...incomingNotifications,
        ])
    }

    const restoreClearedNotifications = (previousNotifications: NormalizedAppNotification[]) => {
        const previousIds = new Set(previousNotifications.map((notification) => notification.id))
        const concurrentRealtimeNotifications = appNotifications.value.filter(
            (notification) => !previousIds.has(notification.id)
        )

        appNotifications.value = trimNotificationList([
            ...concurrentRealtimeNotifications,
            ...previousNotifications,
        ])
    }

    const restoreDeletedNotification = (
        previousNotifications: NormalizedAppNotification[],
        removedNotification: NormalizedAppNotification
    ) => {
        if (appNotifications.value.some((notification) => notification.id === removedNotification.id)) {
            return
        }

        const previousIds = previousNotifications.map((notification) => notification.id)
        const removedIndex = previousIds.findIndex((notificationId) => notificationId === removedNotification.id)
        const nextNotifications = [...appNotifications.value]

        for (let index = removedIndex - 1; index >= 0; index -= 1) {
            const currentIndex = nextNotifications.findIndex(
                (notification) => notification.id === previousIds[index]
            )
            if (currentIndex !== -1) {
                nextNotifications.splice(currentIndex + 1, 0, removedNotification)
                appNotifications.value = trimNotificationList(nextNotifications)
                return
            }
        }

        for (let index = removedIndex + 1; index < previousIds.length; index += 1) {
            const currentIndex = nextNotifications.findIndex(
                (notification) => notification.id === previousIds[index]
            )
            if (currentIndex !== -1) {
                nextNotifications.splice(currentIndex, 0, removedNotification)
                appNotifications.value = trimNotificationList(nextNotifications)
                return
            }
        }

        const previousIdSet = new Set(previousIds)
        const firstKnownIndex = nextNotifications.findIndex((notification) => previousIdSet.has(notification.id))
        const insertAt = firstKnownIndex === -1 ? nextNotifications.length : firstKnownIndex
        nextNotifications.splice(insertAt, 0, removedNotification)
        appNotifications.value = trimNotificationList(nextNotifications)
    }

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
        const normalizedId = normalizeNotificationId(notification.id)
        const existingIndex = appNotifications.value.findIndex(
            (existingNotification) => existingNotification.id === normalizedId
        )
        const existingNotification = existingIndex >= 0 ? appNotifications.value[existingIndex] : undefined
        const normalized = withClientReceivedAt(notification, Date.now(), existingNotification)

        if (existingIndex >= 0) {
            appNotifications.value[existingIndex] = normalized
        } else {
            appNotifications.value = trimNotificationList([normalized, ...appNotifications.value])
        }

        return normalized
    }

    const fetchHistory = async () => {
        isLoadingHistory.value = true
        const fetchStartedAt = Date.now()
        try {
            const res = await apiFetch('/api/notifications/')
            if (res.ok) {
                const data = await res.json()
                const existingById = new Map(
                    appNotifications.value.map((notification) => [notification.id, notification])
                )
                const normalizedHistory = data.map((notification: AppRealtimeNotificationPayload) => {
                    const normalized = normalizeAppNotificationPayload(notification)
                    return withClientReceivedAt(
                        notification,
                        fetchStartedAt,
                        existingById.get(normalized.id)
                    )
                })
                replaceHistoryPreservingConcurrentRealtime(normalizedHistory, fetchStartedAt)
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
        const notificationIdsToMark = new Set(
            appNotifications.value.map((notification) => notification.id)
        )

        try {
             await apiFetch('/api/notifications/mark-all-read', { method: 'POST' })
             appNotifications.value.forEach((notification) => {
                 if (notificationIdsToMark.has(notification.id)) {
                     notification.is_read = true
                 }
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
            restoreClearedNotifications(originalList)
            console.error('Clear all notifications failed:', e)
        }
    }

    const deleteNotification = async (id: number | string) => {
        const originalList = [...appNotifications.value]
        const removedNotification = originalList.find((notification) => notification.id === id)
        if (!removedNotification) return

        appNotifications.value = appNotifications.value.filter((notification) => notification.id !== id)
        
        try {
            const res = await apiFetch(`/api/notifications/${id}`, { method: 'DELETE' })
            if (!res.ok) throw new Error()
        } catch (e) {
            restoreDeletedNotification(originalList, removedNotification)
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
        }, TOAST_LIFETIME_MS)
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
