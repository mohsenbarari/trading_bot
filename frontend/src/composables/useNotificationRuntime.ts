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

function buildChatNotificationBody(payload: ChatRealtimeNotificationPayload): string {
    if (payload.message_type === 'image') return 'تصویر'
    if (payload.message_type === 'video') return 'ویدئو'
    if (payload.message_type === 'sticker') return 'استیکر'
    return payload.content || 'فایل جدید'
}

export function useNotificationRuntime({ connect, on, off, ensureSessionValidation }: NotificationRuntimeOptions) {
    const route = useRoute()
    const router = useRouter()
    const notificationStore = useNotificationStore()
    let hasBootstrappedAuthenticatedRuntime = false

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
        void notificationStore.fetchInitialCounts()
    }

    const bootstrapAuthenticatedRuntime = () => {
        const authToken = localStorage.getItem('auth_token')
        if (!authToken) {
            hasBootstrappedAuthenticatedRuntime = false
            return
        }

        connect()

        if (hasBootstrappedAuthenticatedRuntime) return
        hasBootstrappedAuthenticatedRuntime = true

        void notificationStore.fetchInitialCounts()
        void ensureSessionValidation()
    }

    const handleAppMessage = (payload: AppRealtimeNotificationPayload) => {
        const normalizedNotification = notificationStore.addAppNotification(payload)
        if (route.path === '/notifications') return

        const title = normalizedNotification.title || 'اعلان جدید'
        const body = normalizedNotification.body || ''

        notificationStore.addToast({
            title,
            body,
            route: '/notifications',
            kind: 'app',
            level: normalizedNotification.level,
            category: normalizedNotification.category,
        })

        if (document.hidden) {
            showBrowserNotification(title, body, { route: '/notifications' })
        }
    }

    const handleChatMessage = (payload: ChatRealtimeNotificationPayload) => {
        const senderId = Number(payload.sender_id)
        const isChatOpen = route.path === '/chat'
        const currentChatId = route.query.user_id ? Number(route.query.user_id) : null
        const isViewingSameChat = isChatOpen && currentChatId !== null && currentChatId === senderId && !document.hidden
        const shouldTreatAsUnread = !isViewingSameChat

        if (shouldTreatAsUnread) {
            notificationStore.incrementChatUnread(senderId)
        }

        if (!shouldTreatAsUnread) return

        const senderName = payload.sender_name || 'پیام جدید'
        const body = buildChatNotificationBody(payload)
        const routePath = buildChatNotificationRoute(senderId, senderName)

        notificationStore.addToast({
            title: senderName,
            body,
            route: routePath,
            kind: 'chat',
        })

        if (document.hidden) {
            showBrowserNotification(senderName, body, { route: routePath })
        }
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
        off(WS_NOTIFICATION_EVENTS.sessionRevoked, handleSessionRevoked)
        off(WS_NOTIFICATION_EVENTS.wsReconnect, handleReconnect)
        off(WS_NOTIFICATION_EVENTS.appMessage, handleAppMessage)
        off(WS_NOTIFICATION_EVENTS.chatMessage, handleChatMessage)

        window.removeEventListener('click', handleFirstInteraction)
        window.removeEventListener('touchstart', handleFirstInteraction)
        window.removeEventListener(BROWSER_NOTIFICATION_CLICK_EVENT, handleBrowserNotificationClick)
    })
}