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
    const unreadMentionCount = ref(0)
    const unreadMentionChats = ref<number[]>([])
    const mutedConversationIds = ref<number[]>([])
    const appNotifications = ref<NormalizedAppNotification[]>([])
    const activeToasts = ref<ToastNotification[]>([])
    const isLoadingHistory = ref(false)
    const MAX_IN_MEMORY_NOTIFICATIONS = 100
    const NOTIFICATION_HISTORY_LIMIT = 50
    const TOAST_LIFETIME_MS = 5000
    let clientReceivedAtCursor = 0

    const normalizeConversationKey = (value: unknown): number | null => {
        const conversationKey = Number(value)
        if (!Number.isFinite(conversationKey) || conversationKey === 0) return null
        return conversationKey
    }

    const mergeConversationKeys = (current: number[], incoming: Array<unknown>) => {
        const merged = new Set(current)
        for (const value of incoming) {
            const conversationKey = normalizeConversationKey(value)
            if (conversationKey !== null) {
                merged.add(conversationKey)
            }
        }
        return Array.from(merged)
    }

    const trimNotificationList = (notifications: NormalizedAppNotification[]) =>
        notifications.slice(0, MAX_IN_MEMORY_NOTIFICATIONS)

    const reserveClientReceivedAt = () => {
        const now = Date.now()
        clientReceivedAtCursor = Math.max(clientReceivedAtCursor + 1, now)
        return clientReceivedAtCursor
    }

    const withClientReceivedAt = (
        notification: AppRealtimeNotificationPayload,
        fallbackTimestamp = reserveClientReceivedAt(),
        existingNotification?: NormalizedAppNotification
    ): NormalizedAppNotification => ({
        ...(existingNotification || {}),
        ...normalizeAppNotificationPayload(notification),
        client_received_at:
            existingNotification?.client_received_at
            ?? (typeof notification.client_received_at === 'number' ? notification.client_received_at : fallbackTimestamp),
    })

    const sortNotificationsByRecency = (notifications: NormalizedAppNotification[]) =>
        notifications.sort((left, right) => {
            const leftReceivedAt = typeof left.client_received_at === 'number' ? left.client_received_at : 0
            const rightReceivedAt = typeof right.client_received_at === 'number' ? right.client_received_at : 0
            if (leftReceivedAt !== rightReceivedAt) {
                return rightReceivedAt - leftReceivedAt
            }

            const leftNumericId = Number(left.id)
            const rightNumericId = Number(right.id)
            if (Number.isFinite(leftNumericId) && Number.isFinite(rightNumericId) && leftNumericId !== rightNumericId) {
                return rightNumericId - leftNumericId
            }

            return String(right.id).localeCompare(String(left.id))
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

    const syncUnreadChatIds = (
        conversations: Array<{ user_id?: unknown; unread_mention_count?: unknown }> = [],
        fallbackCount = 0,
        fallbackMentionCount = 0
    ) => {
        const nextIds = Array.from(new Set(
            conversations
                .map((conversation) => normalizeConversationKey(conversation?.user_id))
                .filter((userId): userId is number => userId !== null)
        ))

        unreadChatUserIds.value = nextIds
        chatUnreadCount.value = nextIds.length > 0 || fallbackCount === 0
            ? nextIds.length
            : fallbackCount

        const nextMentionIds = conversations
            .filter((conversation) => Number(conversation?.unread_mention_count || 0) > 0)
            .map((conversation) => normalizeConversationKey(conversation?.user_id))
            .filter((userId): userId is number => userId !== null)

        unreadMentionChats.value = nextMentionIds
        unreadMentionCount.value = nextMentionIds.length > 0
            ? nextMentionIds.length
            : fallbackMentionCount
    }

    const syncMutedConversationIds = (conversationIds: unknown[] = []) => {
        mutedConversationIds.value = Array.from(new Set(
            conversationIds
                .map((conversationId) => normalizeConversationKey(conversationId))
                .filter((conversationId): conversationId is number => conversationId !== null)
        ))
    }

    const setConversationMuted = (conversationId: unknown, muted: boolean) => {
        const normalizedConversationId = normalizeConversationKey(conversationId)
        if (normalizedConversationId === null) return

        if (muted) {
            if (!mutedConversationIds.value.includes(normalizedConversationId)) {
                mutedConversationIds.value = [...mutedConversationIds.value, normalizedConversationId]
            }
            return
        }

        mutedConversationIds.value = mutedConversationIds.value.filter((id) => id !== normalizedConversationId)
    }

    const isConversationMuted = (conversationId: unknown) => {
        const normalizedConversationId = normalizeConversationKey(conversationId)
        if (normalizedConversationId === null) return false
        return mutedConversationIds.value.includes(normalizedConversationId)
    }

    const fetchInitialCounts = async () => {
        const token = localStorage.getItem('auth_token')
        if (!token) return

        try {
            const response = await apiFetch('/api/chat/poll')
            if (response.ok) {
                const data = await response.json()
                syncUnreadChatIds(
                    data.conversations_with_unread || [],
                    data.unread_chats_count || 0,
                    data.total_unread_mentions || 0
                )
                syncMutedConversationIds(data.muted_conversation_ids || [])
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
        incrementChatUnreadBatch([userId])
    }

    const incrementChatUnreadBatch = (conversationIds: Array<number | null | undefined>) => {
        const validConversationIds = conversationIds.filter(
            (conversationId): conversationId is number => normalizeConversationKey(conversationId) !== null
        )
        if (validConversationIds.length === 0) {
            void fetchInitialCounts()
            return
        }

        const nextIds = mergeConversationKeys(unreadChatUserIds.value, validConversationIds)
        if (nextIds.length === unreadChatUserIds.value.length) {
            return
        }

        unreadChatUserIds.value = nextIds
        chatUnreadCount.value = unreadChatUserIds.value.length
    }

    const incrementMentionUnread = (userId?: number | null) => {
        incrementMentionUnreadBatch([userId])
    }

    const incrementMentionUnreadBatch = (conversationIds: Array<number | null | undefined>) => {
        const nextIds = mergeConversationKeys(unreadMentionChats.value, conversationIds)
        if (nextIds.length === unreadMentionChats.value.length) {
            return
        }

        unreadMentionChats.value = nextIds
        unreadMentionCount.value = unreadMentionChats.value.length
    }

    const markChatAsRead = (userId?: number | null) => {
        const normalizedConversationId = normalizeConversationKey(userId)
        if (normalizedConversationId === null) return
        unreadChatUserIds.value = unreadChatUserIds.value.filter((id) => id !== normalizedConversationId)
        chatUnreadCount.value = unreadChatUserIds.value.length

        unreadMentionChats.value = unreadMentionChats.value.filter((id) => id !== normalizedConversationId)
        unreadMentionCount.value = unreadMentionChats.value.length
    }

    const addAppNotification = (notification: AppRealtimeNotificationPayload) => {
        return addAppNotificationsBatch([notification])[0]!
    }

    const addAppNotificationsBatch = (notifications: AppRealtimeNotificationPayload[]) => {
        if (notifications.length === 0) return [] as NormalizedAppNotification[]

        const existingById = new Map(
            appNotifications.value.map((notification) => [notification.id, notification] as const)
        )
        const nextById = new Map(existingById)
        const normalizedBatch: NormalizedAppNotification[] = []

        for (const notification of notifications) {
            const normalizedId = normalizeNotificationId(notification.id)
            const existingNotification = nextById.get(normalizedId)
            const normalized = withClientReceivedAt(notification, reserveClientReceivedAt(), existingNotification)
            nextById.set(normalizedId, normalized)
            normalizedBatch.push(normalized)
        }

        appNotifications.value = trimNotificationList(sortNotificationsByRecency(Array.from(nextById.values())))
        return normalizedBatch
    }

    const fetchHistory = async () => {
        isLoadingHistory.value = true
        const fetchStartedAt = Date.now()
        try {
            const response = await apiFetch(`/api/notifications/?limit=${NOTIFICATION_HISTORY_LIMIT}&offset=0`)
            if (response.ok) {
                const data = await response.json()
                const existingById = new Map(
                    appNotifications.value.map((notification) => [notification.id, notification] as const)
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
        } catch (error) {
            console.error('Failed to fetch notifications history:', error)
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
            const response = await apiFetch('/api/notifications/mark-all-read', { method: 'POST' })
            if (!response.ok) throw new Error()
            appNotifications.value = appNotifications.value.map((notification) => ({ ...notification, is_read: true }))
        } catch (error) {
            console.error('Failed to mark all as read:', error)
        }
    }

    const toggleReadStatus = async (id: number | string, isRead: boolean) => {
        const notification = appNotifications.value.find((item) => item.id === id)
        if (!notification) return

        const originalState = notification.is_read
        notification.is_read = isRead

        try {
            const response = await apiFetch(`/api/notifications/${id}/read`, {
                method: 'PATCH',
                body: JSON.stringify({ is_read: isRead }),
            })
            if (!response.ok) throw new Error()
        } catch (error) {
            notification.is_read = originalState
            console.error('Failed to toggle read status:', error)
        }
    }

    const clearAllNotifications = async () => {
        const originalList = [...appNotifications.value]
        appNotifications.value = []

        try {
            const response = await apiFetch('/api/notifications/', { method: 'DELETE' })
            if (!response.ok) throw new Error()
        } catch (error) {
            restoreClearedNotifications(originalList)
            console.error('Clear all notifications failed:', error)
        }
    }

    const deleteNotification = async (id: number | string) => {
        const originalList = [...appNotifications.value]
        const removedNotification = originalList.find((notification) => notification.id === id)
        if (!removedNotification) return

        appNotifications.value = appNotifications.value.filter((notification) => notification.id !== id)

        try {
            const response = await apiFetch(`/api/notifications/${id}`, { method: 'DELETE' })
            if (!response.ok) throw new Error()
        } catch (error) {
            restoreDeletedNotification(originalList, removedNotification)
            console.error('Delete failed:', error)
        }
    }

    const addToast = (toast: ToastInput) => {
        addToastsBatch([toast])
    }

    const addToastsBatch = (toasts: ToastInput[]) => {
        if (toasts.length === 0) return

        const nextToasts = toasts.map((toast) => createToastNotification(toast))
        activeToasts.value = [...activeToasts.value, ...nextToasts]

        playNotificationSound()

        for (const toast of nextToasts) {
            window.setTimeout(() => {
                removeToast(toast.id)
            }, TOAST_LIFETIME_MS)
        }
    }

    const removeToast = (id: number) => {
        activeToasts.value = activeToasts.value.filter((toast) => toast.id !== id)
    }

    return {
        chatUnreadCount,
        unreadChatUserIds,
        unreadMentionCount,
        unreadMentionChats,
        incrementMentionUnread,
        incrementMentionUnreadBatch,
        mutedConversationIds,
        appNotifications,
        activeToasts,
        fetchInitialCounts,
        setChatUnreadCount,
        incrementChatUnread,
        incrementChatUnreadBatch,
        markChatAsRead,
        syncUnreadChatIds,
        syncMutedConversationIds,
        setConversationMuted,
        isConversationMuted,
        addAppNotification,
        addAppNotificationsBatch,
        addToast,
        addToastsBatch,
        removeToast,
        isLoadingHistory,
        fetchHistory,
        openNotificationCenter,
        markAllAsRead,
        clearAllNotifications,
        deleteNotification,
        toggleReadStatus,
    }
})
