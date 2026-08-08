import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { playNotificationSound } from '../utils/audio'
import { routeRequest, routeRequestJson } from '../utils/routeRequest'
import {
    createToastNotification,
    normalizeAppNotificationPayload,
    normalizeNotificationId,
    type AppRealtimeNotificationPayload,
    type NormalizedAppNotification,
    type ToastInput,
    type ToastNotification,
} from '../types/notifications'

export type NotificationHistoryStatus = 'idle' | 'loading' | 'success' | 'error'

export type NotificationHistoryResult =
    | { ok: true; count: number }
    | { ok: false; error: string }

const NOTIFICATION_HISTORY_ERROR = 'دریافت اعلان‌ها انجام نشد.'

export const useNotificationStore = defineStore('notifications', () => {
    const chatUnreadCount = ref(0)
    const unreadChatUserIds = ref<number[]>([])
    const unreadMentionCount = ref(0)
    const unreadMentionChats = ref<number[]>([])
    const mutedConversationIds = ref<number[]>([])
    const appNotifications = ref<NormalizedAppNotification[]>([])
    const activeToasts = ref<ToastNotification[]>([])
    const isLoadingHistory = ref(false)
    const isRefreshingHistory = ref(false)
    const historyStatus = ref<NotificationHistoryStatus>('idle')
    const historyError = ref<string | null>(null)
    const hasLoadedHistory = ref(false)
    const MAX_IN_MEMORY_NOTIFICATIONS = 100
    const NOTIFICATION_HISTORY_LIMIT = 50
    const TOAST_LIFETIME_MS = 5000
    let clientReceivedAtCursor = 0
    let notificationMutationCursor = 0
    let activeHistoryRequest: Promise<NotificationHistoryResult> | null = null
    let activeOpenNotificationCenterRequest: Promise<NotificationHistoryResult> | null = null
    const realtimeRevisionById = new Map<number | string, number>()

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
        fetchStartedRevision: number
    ) => {
        const incomingById = new Map<number | string, NormalizedAppNotification>()
        for (const notification of incomingNotifications) {
            incomingById.set(notification.id, notification)
        }

        for (const [notificationId, incomingNotification] of incomingById) {
            if ((realtimeRevisionById.get(notificationId) ?? 0) <= fetchStartedRevision) continue

            const realtimeNotification = appNotifications.value.find(
                (notification) => notification.id === notificationId
            )
            if (realtimeNotification) {
                incomingById.set(notificationId, {
                    ...incomingNotification,
                    ...realtimeNotification,
                })
            }
        }

        const incomingIds = new Set(incomingById.keys())
        const concurrentRealtimeNotifications = appNotifications.value.filter((notification) =>
            !incomingIds.has(notification.id)
            && (realtimeRevisionById.get(notification.id) ?? 0) > fetchStartedRevision
        )

        appNotifications.value = trimNotificationList([
            ...concurrentRealtimeNotifications,
            ...incomingById.values(),
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
            notificationMutationCursor += 1
            realtimeRevisionById.set(normalizedId, notificationMutationCursor)
            normalizedBatch.push(normalized)
        }

        appNotifications.value = trimNotificationList(sortNotificationsByRecency(Array.from(nextById.values())))
        if (historyStatus.value === 'loading' && appNotifications.value.length > 0) {
            isLoadingHistory.value = false
            isRefreshingHistory.value = true
        }
        return normalizedBatch
    }

    const fetchHistory = (): Promise<NotificationHistoryResult> => {
        if (activeHistoryRequest) return activeHistoryRequest

        const historySnapshotReceivedAt = reserveClientReceivedAt()
        const fetchStartedRevision = notificationMutationCursor
        const hasRetainedRows = appNotifications.value.length > 0
        isLoadingHistory.value = !hasLoadedHistory.value && !hasRetainedRows
        isRefreshingHistory.value = hasLoadedHistory.value || hasRetainedRows
        historyStatus.value = 'loading'
        historyError.value = null

        activeHistoryRequest = (async (): Promise<NotificationHistoryResult> => {
            try {
                const data = await routeRequestJson<AppRealtimeNotificationPayload[]>(
                    `/api/notifications/?limit=${NOTIFICATION_HISTORY_LIMIT}&offset=0`,
                    {
                        errorContext: {
                            surface: 'settings',
                            scope: 'list',
                            operation: hasRetainedRows ? 'background-refresh' : 'initial-load',
                            preserveExistingData: hasRetainedRows,
                            resourceLabel: 'اعلان‌ها',
                            fallbackMessage: NOTIFICATION_HISTORY_ERROR,
                        },
                    }
                )
                if (!Array.isArray(data)) {
                    throw new Error('Notification history response is not an array')
                }

                const existingById = new Map(
                    appNotifications.value.map((notification) => [notification.id, notification] as const)
                )
                const normalizedHistory = data.map((notification: AppRealtimeNotificationPayload) => {
                    const normalized = normalizeAppNotificationPayload(notification)
                    return withClientReceivedAt(
                        notification,
                        historySnapshotReceivedAt,
                        existingById.get(normalized.id)
                    )
                })
                replaceHistoryPreservingConcurrentRealtime(normalizedHistory, fetchStartedRevision)
                hasLoadedHistory.value = true
                historyStatus.value = 'success'
                return { ok: true, count: new Set(normalizedHistory.map((notification) => notification.id)).size }
            } catch (error) {
                historyStatus.value = 'error'
                historyError.value = NOTIFICATION_HISTORY_ERROR
                console.error('Failed to fetch notifications history:', error)
                return { ok: false, error: NOTIFICATION_HISTORY_ERROR }
            } finally {
                isLoadingHistory.value = false
                isRefreshingHistory.value = false
                activeHistoryRequest = null
            }
        })()

        return activeHistoryRequest
    }

    const openNotificationCenter = (): Promise<NotificationHistoryResult> => {
        if (activeOpenNotificationCenterRequest) return activeOpenNotificationCenterRequest

        activeOpenNotificationCenterRequest = (async () => {
            const historyResult = await fetchHistory()
            if (!historyResult.ok) return historyResult

            await markAllAsRead()
            return historyResult
        })().finally(() => {
            activeOpenNotificationCenterRequest = null
        })

        return activeOpenNotificationCenterRequest
    }

    const markAllAsRead = async () => {
        const markStartedRevision = notificationMutationCursor
        try {
            await routeRequest('/api/notifications/mark-all-read', {
                method: 'POST',
                errorContext: {
                    surface: 'settings',
                    scope: 'action',
                    operation: 'update',
                    fallbackMessage: 'ثبت وضعیت خوانده‌شده انجام نشد.',
                },
            })
            appNotifications.value = appNotifications.value.map((notification) => (
                (realtimeRevisionById.get(notification.id) ?? 0) <= markStartedRevision
                    ? { ...notification, is_read: true }
                    : notification
            ))
            return { ok: true as const }
        } catch (error) {
            console.error('Failed to mark all as read:', error)
            return { ok: false as const }
        }
    }

    const toggleReadStatus = async (id: number | string, isRead: boolean) => {
        const notification = appNotifications.value.find((item) => item.id === id)
        if (!notification) return

        const originalState = notification.is_read
        notification.is_read = isRead

        try {
            await routeRequest(`/api/notifications/${id}/read`, {
                method: 'PATCH',
                body: JSON.stringify({ is_read: isRead }),
                errorContext: {
                    surface: 'settings',
                    scope: 'item',
                    operation: 'update',
                    fallbackMessage: 'تغییر وضعیت اعلان انجام نشد.',
                },
            })
        } catch (error) {
            notification.is_read = originalState
            console.error('Failed to toggle read status:', error)
        }
    }

    const clearAllNotifications = async () => {
        const originalList = [...appNotifications.value]
        appNotifications.value = []

        try {
            await routeRequest('/api/notifications/', {
                method: 'DELETE',
                errorContext: {
                    surface: 'settings',
                    scope: 'action',
                    operation: 'delete',
                    fallbackMessage: 'پاک‌کردن اعلان‌ها انجام نشد.',
                },
            })
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
            await routeRequest(`/api/notifications/${id}`, {
                method: 'DELETE',
                errorContext: {
                    surface: 'settings',
                    scope: 'item',
                    operation: 'delete',
                    fallbackMessage: 'حذف اعلان انجام نشد.',
                },
            })
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
        isRefreshingHistory,
        historyStatus,
        historyError,
        hasLoadedHistory,
        fetchHistory,
        openNotificationCenter,
        markAllAsRead,
        clearAllNotifications,
        deleteNotification,
        toggleReadStatus,
    }
})
