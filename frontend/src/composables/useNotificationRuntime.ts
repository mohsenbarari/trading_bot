import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import {
    type BrowserNotificationClickDetail,
    type ChatRealtimeNotificationPayload,
    type AppRealtimeNotificationPayload,
    WS_NOTIFICATION_EVENTS,
} from '../types/notifications'
import {
    BROWSER_NOTIFICATION_CLICK_EVENT,
    requestNotificationPermission,
    showBrowserNotification,
} from '../utils/browserNotifications'
import { unlockAudioContext } from '../utils/audio'
import { resolveRoomConversationKey } from '../utils/chatRoomRouting'
import { currentUserSummary } from '../utils/currentUser'

type WebSocketEventHandler<T = any> = (data: T) => void

interface NotificationRuntimeOptions {
    connect: () => void
    on: (event: string, callback: WebSocketEventHandler) => void
    off: (event: string, callback: WebSocketEventHandler) => void
    ensureSessionValidation: () => void | Promise<void>
}

function buildChatNotificationRoute(senderId: number, senderName: string): string {
    return `/chat?user_id=${senderId}&user_name=${encodeURIComponent(senderName)}`
}

function resolveRealtimeConversationKey(payload: ChatRealtimeNotificationPayload): number | null {
    const roomConversationKey = resolveRoomConversationKey(payload.room_kind, payload.chat_id)
    if (roomConversationKey !== null) {
        return roomConversationKey
    }

    const senderId = Number(payload.sender_id)
    return Number.isFinite(senderId) ? senderId : null
}

function buildRealtimeConversationLabel(payload: ChatRealtimeNotificationPayload): string {
    if (payload.room_kind === 'channel') {
        return payload.conversation_title || 'کانال'
    }
    if (payload.room_kind === 'group') {
        return payload.conversation_title || 'گروه'
    }
    return payload.sender_name || 'پیام جدید'
}

function buildChatNotificationBody(payload: ChatRealtimeNotificationPayload): string {
    if (payload.message_type === 'image') return 'تصویر'
    if (payload.message_type === 'video') return 'ویدئو'
    if (payload.message_type === 'sticker') return 'استیکر'
    return payload.content || 'فایل جدید'
}

function hasRealtimeMention(payload: ChatRealtimeNotificationPayload, currentUserId: number | null | undefined): boolean {
    if (!currentUserId) return false
    if (payload.mention_all === true) return true
    if (!Array.isArray(payload.mentions)) return false

    return payload.mentions.some((mentionedUserId) => Number(mentionedUserId) === currentUserId)
}

export function useNotificationRuntime({ connect, on, off, ensureSessionValidation }: NotificationRuntimeOptions) {
    const route = useRoute()
    const router = useRouter()
    const notificationStore = useNotificationStore()
    let hasBootstrappedAuthenticatedRuntime = false
    let skipNextReconnectCountsFetch = false
    let flushScheduled = false
    let isUnmounted = false
    const pendingAppMessages: AppRealtimeNotificationPayload[] = []
    const pendingChatMessages: ChatRealtimeNotificationPayload[] = []

    const addAppNotificationsBatch = (payloads: AppRealtimeNotificationPayload[]) => {
        const batchAdd = (notificationStore as {
            addAppNotificationsBatch?: (items: AppRealtimeNotificationPayload[]) => ReturnType<typeof notificationStore.addAppNotification>[]
        }).addAppNotificationsBatch
        if (typeof batchAdd === 'function') {
            return batchAdd(payloads)
        }
        return payloads.map((payload) => notificationStore.addAppNotification(payload))
    }

    const addToastsBatch = (toasts: Parameters<typeof notificationStore.addToast>[0][]) => {
        if (toasts.length === 0) return
        const batchAdd = (notificationStore as {
            addToastsBatch?: (items: Parameters<typeof notificationStore.addToast>[0][]) => void
        }).addToastsBatch
        if (typeof batchAdd === 'function') {
            batchAdd(toasts)
            return
        }
        toasts.forEach((toast) => notificationStore.addToast(toast))
    }

    const incrementChatUnreadBatch = (conversationKeys: number[]) => {
        if (conversationKeys.length === 0) return
        const batchIncrement = (notificationStore as {
            incrementChatUnreadBatch?: (items: number[]) => void
        }).incrementChatUnreadBatch
        if (typeof batchIncrement === 'function') {
            batchIncrement(conversationKeys)
            return
        }
        conversationKeys.forEach((conversationKey) => notificationStore.incrementChatUnread(conversationKey))
    }

    const incrementMentionUnreadBatch = (conversationKeys: number[]) => {
        if (conversationKeys.length === 0) return
        const batchIncrement = (notificationStore as {
            incrementMentionUnreadBatch?: (items: number[]) => void
        }).incrementMentionUnreadBatch
        if (typeof batchIncrement === 'function') {
            batchIncrement(conversationKeys)
            return
        }
        conversationKeys.forEach((conversationKey) => notificationStore.incrementMentionUnread(conversationKey))
    }

    const flushPendingNotifications = () => {
        flushScheduled = false
        if (isUnmounted) {
            pendingAppMessages.length = 0
            pendingChatMessages.length = 0
            return
        }

        if (pendingAppMessages.length > 0) {
            const appPayloads = pendingAppMessages.splice(0, pendingAppMessages.length)
            const normalizedNotifications = addAppNotificationsBatch(appPayloads)

            if (route.path !== '/notifications') {
                addToastsBatch(normalizedNotifications.map((notification) => {
                    const title = notification.title || 'اعلان جدید'
                    const body = notification.body || ''
                    const targetRoute = typeof notification.route === 'string' && notification.route.trim()
                        ? notification.route
                        : '/notifications'

                    if (document.hidden) {
                        showBrowserNotification(title, body, { route: targetRoute })
                    }

                    return {
                        title,
                        body,
                        route: targetRoute,
                        kind: 'app' as const,
                        level: notification.level,
                        category: notification.category,
                    }
                }))
            }
        }

        if (pendingChatMessages.length > 0) {
            const chatPayloads = pendingChatMessages.splice(0, pendingChatMessages.length)
            const unreadConversationKeys: number[] = []
            const mentionConversationKeys: number[] = []
            const toastBatch: Parameters<typeof notificationStore.addToast>[0][] = []

            for (const payload of chatPayloads) {
                const conversationKey = resolveRealtimeConversationKey(payload)
                if (conversationKey === null) continue

                const isChatOpen = route.path === '/chat'
                const currentChatId = route.query.user_id ? Number(route.query.user_id) : null
                const isViewingSameChat = isChatOpen
                    && currentChatId !== null
                    && currentChatId === conversationKey
                    && !document.hidden
                const shouldTreatAsUnread = !isViewingSameChat
                const isMutedConversation = notificationStore.isConversationMuted(conversationKey)

                const currentUserId = currentUserSummary.value?.id
                const isMentioned = hasRealtimeMention(payload, currentUserId)

                if (shouldTreatAsUnread) {
                    unreadConversationKeys.push(conversationKey)
                    if (isMentioned) {
                        mentionConversationKeys.push(conversationKey)
                    }
                }

                if (!shouldTreatAsUnread || (isMutedConversation && !isMentioned)) {
                    continue
                }

                const senderName = buildRealtimeConversationLabel(payload)
                const body = buildChatNotificationBody(payload)
                const routePath = buildChatNotificationRoute(conversationKey, senderName)

                toastBatch.push({
                    title: senderName,
                    body,
                    route: routePath,
                    kind: 'chat',
                })

                if (document.hidden && payload.room_kind !== 'channel') {
                    showBrowserNotification(senderName, body, { route: routePath })
                }
            }

            incrementChatUnreadBatch(unreadConversationKeys)
            incrementMentionUnreadBatch(mentionConversationKeys)
            addToastsBatch(toastBatch)
        }
    }

    const scheduleNotificationFlush = () => {
        if (flushScheduled) return
        flushScheduled = true
        Promise.resolve().then(flushPendingNotifications)
    }

    const handleBrowserNotificationClick = (event: Event) => {
        const targetRoute = (event as CustomEvent<BrowserNotificationClickDetail>).detail?.route
        if (!targetRoute || router.currentRoute.value.fullPath === targetRoute) return
        void router.push(targetRoute)
    }

    const handleFirstInteraction = () => {
        void requestNotificationPermission()
        unlockAudioContext()
        window.removeEventListener('click', handleFirstInteraction)
        window.removeEventListener('touchstart', handleFirstInteraction)
    }

    const handleSessionRevoked = () => {
        void ensureSessionValidation()
    }

    const handleReconnect = () => {
        if (skipNextReconnectCountsFetch) {
            skipNextReconnectCountsFetch = false
            return
        }
        void notificationStore.fetchInitialCounts()
    }

    const bootstrapAuthenticatedRuntime = () => {
        const authToken = localStorage.getItem('auth_token')
        if (!authToken) {
            hasBootstrappedAuthenticatedRuntime = false
            skipNextReconnectCountsFetch = false
            return
        }

        if (!hasBootstrappedAuthenticatedRuntime) {
            skipNextReconnectCountsFetch = true
        }
        connect()

        if (hasBootstrappedAuthenticatedRuntime) return
        hasBootstrappedAuthenticatedRuntime = true

        void notificationStore.fetchInitialCounts()
        void ensureSessionValidation()
    }

    const handleAppMessage = (payload: AppRealtimeNotificationPayload) => {
        pendingAppMessages.push(payload)
        scheduleNotificationFlush()
    }

    const handleChatMessage = (payload: ChatRealtimeNotificationPayload) => {
        pendingChatMessages.push(payload)
        scheduleNotificationFlush()
    }

    onMounted(() => {
        bootstrapAuthenticatedRuntime()

        on(WS_NOTIFICATION_EVENTS.sessionRevoked, handleSessionRevoked)
        on(WS_NOTIFICATION_EVENTS.wsReconnect, handleReconnect)
        on(WS_NOTIFICATION_EVENTS.appMessage, handleAppMessage)
        on(WS_NOTIFICATION_EVENTS.chatMessage, handleChatMessage)

        window.addEventListener('click', handleFirstInteraction)
        window.addEventListener('touchstart', handleFirstInteraction)
        window.addEventListener(BROWSER_NOTIFICATION_CLICK_EVENT, handleBrowserNotificationClick)
    })

    watch(() => route.fullPath, () => {
        bootstrapAuthenticatedRuntime()
    })

    onBeforeUnmount(() => {
        isUnmounted = true
        flushScheduled = false
        pendingAppMessages.length = 0
        pendingChatMessages.length = 0
        off(WS_NOTIFICATION_EVENTS.sessionRevoked, handleSessionRevoked)
        off(WS_NOTIFICATION_EVENTS.wsReconnect, handleReconnect)
        off(WS_NOTIFICATION_EVENTS.appMessage, handleAppMessage)
        off(WS_NOTIFICATION_EVENTS.chatMessage, handleChatMessage)

        window.removeEventListener('click', handleFirstInteraction)
        window.removeEventListener('touchstart', handleFirstInteraction)
        window.removeEventListener(BROWSER_NOTIFICATION_CLICK_EVENT, handleBrowserNotificationClick)
    })
}
