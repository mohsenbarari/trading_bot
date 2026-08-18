import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import {
  type BrowserNotificationClickDetail,
  type ChatRealtimeNotificationPayload,
  type AppRealtimeNotificationPayload,
  sanitizeNotificationBody,
  sanitizeNotificationTitle,
  WS_NOTIFICATION_EVENTS,
} from '../types/notifications'
import {
  BROWSER_NOTIFICATION_CLICK_EVENT,
  showBrowserNotification,
} from '../utils/browserNotifications'
import { unlockAudioContext } from '../utils/audio'
import { resolveRoomConversationKey } from '../utils/chatRoomRouting'
import { currentUserSummary } from '../utils/currentUser'
import { useConversationsStore } from '../stores/chat/conversations'
import { validateIntendedRoute } from '../utils/authNavigation'
import { enableWebPushNotifications, getWebPushStatus } from '../services/webPush'
import { assertSuccessfulNavigation } from '../utils/navigationResult'

type WebSocketEventHandler<T = unknown> = (data: T) => void

interface NotificationRuntimeOptions {
  connect: () => void
  on: <T>(event: string, callback: WebSocketEventHandler<T>) => void
  off: <T>(event: string, callback: WebSocketEventHandler<T>) => void
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

function buildConversationPreviewContent(payload: ChatRealtimeNotificationPayload): string {
  return buildChatNotificationBody(payload)
}

function hasRealtimeMention(
  payload: ChatRealtimeNotificationPayload,
  currentUserId: number | null | undefined,
): boolean {
  if (!currentUserId) return false
  if (payload.mention_all === true) return true
  if (!Array.isArray(payload.mentions)) return false

  return payload.mentions.some((mentionedUserId) => Number(mentionedUserId) === currentUserId)
}

function uniqueByKey<T>(items: T[], getKey: (item: T) => string | number) {
  const nextByKey = new Map<string | number, T>()
  for (const item of items) {
    nextByKey.set(getKey(item), item)
  }
  return Array.from(nextByKey.values())
}

function buildAppToastCopy(notification: ReturnType<typeof useNotificationStore>['appNotifications'][number]) {
  if (notification.category !== 'trade') {
    return {
      title: sanitizeNotificationTitle(notification.title),
      body: sanitizeNotificationBody(notification.body),
    }
  }

  const tradeNumber = Number(notification.trade_number)
  const tradeLabel = Number.isSafeInteger(tradeNumber) && tradeNumber > 0
    ? `معامله شماره ${tradeNumber.toLocaleString('fa-IR', { useGrouping: false })}`
    : 'معامله جدید'
  return {
    title: 'معامله انجام شد',
    body: `${tradeLabel} با موفقیت ثبت شد.`,
  }
}

export function useNotificationRuntime({
  connect,
  on,
  off,
  ensureSessionValidation,
}: NotificationRuntimeOptions) {
  const route = useRoute()
  const router = useRouter()
  const notificationStore = useNotificationStore()
  const conversationsStore = useConversationsStore()
  let bootstrappedAuthToken: string | null = null
  let skipNextReconnectCountsFetch = false
  let flushScheduled = false
  let isUnmounted = false
  let webPushDeliveryState: 'checking' | 'subscribed' | 'fallback' = 'checking'
  const pendingAppMessages: AppRealtimeNotificationPayload[] = []
  const pendingChatMessages: ChatRealtimeNotificationPayload[] = []

  const addAppNotificationsBatch = (payloads: AppRealtimeNotificationPayload[]) => {
    const batchAdd = (
      notificationStore as {
        addAppNotificationsBatch?: (
          items: AppRealtimeNotificationPayload[],
        ) => ReturnType<typeof notificationStore.addAppNotification>[]
      }
    ).addAppNotificationsBatch
    if (typeof batchAdd === 'function') {
      return batchAdd(payloads)
    }
    return payloads.map((payload) => notificationStore.addAppNotification(payload))
  }

  const addToastsBatch = (toasts: Parameters<typeof notificationStore.addToast>[0][]) => {
    if (toasts.length === 0) return
    const batchAdd = (
      notificationStore as {
        addToastsBatch?: (items: Parameters<typeof notificationStore.addToast>[0][]) => void
      }
    ).addToastsBatch
    if (typeof batchAdd === 'function') {
      batchAdd(toasts)
      return
    }
    toasts.forEach((toast) => notificationStore.addToast(toast))
  }

  const incrementChatUnreadBatch = (conversationKeys: number[]) => {
    if (conversationKeys.length === 0) return
    const batchIncrement = (
      notificationStore as {
        incrementChatUnreadBatch?: (items: number[]) => void
      }
    ).incrementChatUnreadBatch
    if (typeof batchIncrement === 'function') {
      batchIncrement(conversationKeys)
      return
    }
    conversationKeys.forEach((conversationKey) =>
      notificationStore.incrementChatUnread(conversationKey),
    )
  }

  const incrementMentionUnreadBatch = (conversationKeys: number[]) => {
    if (conversationKeys.length === 0) return
    const batchIncrement = (
      notificationStore as {
        incrementMentionUnreadBatch?: (items: number[]) => void
      }
    ).incrementMentionUnreadBatch
    if (typeof batchIncrement === 'function') {
      batchIncrement(conversationKeys)
      return
    }
    conversationKeys.forEach((conversationKey) =>
      notificationStore.incrementMentionUnread(conversationKey),
    )
  }

  const patchConversationFromChatNotification = (
    payload: ChatRealtimeNotificationPayload,
    conversationKey: number,
    options: { unread: boolean },
  ) => {
    const currentConversation = conversationsStore.conversations.find(
      (conversation) => conversation.other_user_id === conversationKey,
    )
    if (!currentConversation) return

    const lastMessageAt = payload.created_at || payload.updated_at || new Date().toISOString()
    conversationsStore.patchConversation(conversationKey, {
      last_message_at: lastMessageAt,
      last_message_type:
        typeof payload.message_type === 'string'
          ? payload.message_type
          : currentConversation.last_message_type,
      last_message_content: buildConversationPreviewContent(payload),
      unread_count: options.unread
        ? Math.max(0, Number(currentConversation.unread_count || 0) + 1)
        : currentConversation.unread_count,
    })
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
      const existingNotificationIds = new Set(
        notificationStore.appNotifications.map((notification) => notification.id),
      )
      const normalizedNotifications = addAppNotificationsBatch(appPayloads)
      const uniqueNotifications = uniqueByKey(
        normalizedNotifications.filter(
          (notification) => !existingNotificationIds.has(notification.id),
        ),
        (notification) => notification.id,
      )

      const isNotificationCenterRoute =
        route.path === '/account/notifications' || route.path === '/notifications'
      const toastItems: Parameters<typeof notificationStore.addToast>[0][] = []
      for (const notification of uniqueNotifications) {
        const canonicalTitle = sanitizeNotificationTitle(notification.title)
        const canonicalBody = sanitizeNotificationBody(notification.body)
        const toastCopy = buildAppToastCopy(notification)
        // Global app toasts always enter through the canonical center. Trade
        // completion is intentionally also surfaced while that center is open,
        // so users without browser-notification permission still get immediate
        // confirmation in every authenticated route.
        const targetRoute = '/account/notifications'

        if (document.hidden && webPushDeliveryState === 'fallback') {
          showBrowserNotification(canonicalTitle, canonicalBody, { route: targetRoute })
        }

        if (!isNotificationCenterRoute || notification.category === 'trade') {
          toastItems.push({
            ...toastCopy,
            route: targetRoute,
            kind: 'app' as const,
            level: notification.level,
            category: notification.category,
          })
        }
      }
      addToastsBatch(toastItems)
    }

    if (pendingChatMessages.length > 0) {
      const chatPayloads = pendingChatMessages.splice(0, pendingChatMessages.length)
      const unreadConversationKeys: number[] = []
      const mentionConversationKeys: number[] = []
      const toastByConversation = new Map<
        number,
        Parameters<typeof notificationStore.addToast>[0]
      >()
      const browserNotificationByConversation = new Map<
        number,
        { title: string; body: string; route: string }
      >()

      for (const payload of chatPayloads) {
        const conversationKey = resolveRealtimeConversationKey(payload)
        if (conversationKey === null) continue

        const isChatOpen = route.path === '/chat'
        const currentChatId = route.query.user_id ? Number(route.query.user_id) : null
        const isViewingSameChat =
          isChatOpen &&
          currentChatId !== null &&
          currentChatId === conversationKey &&
          !document.hidden
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

        patchConversationFromChatNotification(payload, conversationKey, {
          unread: shouldTreatAsUnread,
        })

        if (!shouldTreatAsUnread || (isMutedConversation && !isMentioned)) {
          continue
        }

        const senderName = buildRealtimeConversationLabel(payload)
        const body = buildChatNotificationBody(payload)
        const routePath = buildChatNotificationRoute(conversationKey, senderName)

        toastByConversation.set(conversationKey, {
          title: senderName,
          body,
          route: routePath,
          kind: 'chat',
        })

        if (document.hidden && payload.room_kind !== 'channel') {
          browserNotificationByConversation.set(conversationKey, {
            title: senderName,
            body,
            route: routePath,
          })
        }
      }

      incrementChatUnreadBatch(unreadConversationKeys)
      incrementMentionUnreadBatch(mentionConversationKeys)
      addToastsBatch(Array.from(toastByConversation.values()))
      browserNotificationByConversation.forEach((notification) => {
        showBrowserNotification(notification.title, notification.body, {
          route: notification.route,
        })
      })
    }
  }

  const scheduleNotificationFlush = () => {
    if (flushScheduled) return
    flushScheduled = true
    Promise.resolve().then(flushPendingNotifications)
  }

  const resolveBrowserNotificationRoute = (candidate: unknown): string | null => {
    const safePath = validateIntendedRoute({ fullPath: candidate })
    if (!safePath) return null

    try {
      const resolved = router.resolve(safePath)
      if (!resolved.matched.length || resolved.name === 'system-recovery') return null
      const canonicalResolvedPath = validateIntendedRoute({ fullPath: resolved.fullPath })
      return canonicalResolvedPath
    } catch {
      return null
    }
  }

  const restoreBrowserNotificationContext = async (safePath: string | null) => {
    if (!safePath || router.currentRoute.value.fullPath === safePath) return
    try {
      assertSuccessfulNavigation(await router.replace(safePath))
    } catch {
      // Keep the best context the router could preserve when restoration fails.
    }
  }

  const handleBrowserNotificationClick = async (event: Event) => {
    const targetRoute = resolveBrowserNotificationRoute(
      (event as CustomEvent<BrowserNotificationClickDetail>).detail?.route,
    )
    if (!targetRoute || router.currentRoute.value.fullPath === targetRoute) return

    const previousRoute =
      resolveBrowserNotificationRoute(router.currentRoute.value.fullPath) ??
      resolveBrowserNotificationRoute('/account/notifications')
    try {
      assertSuccessfulNavigation(await router.push(targetRoute))
      if (
        router.currentRoute.value.name === 'system-recovery' ||
        router.currentRoute.value.fullPath !== targetRoute
      ) {
        await restoreBrowserNotificationContext(previousRoute)
      }
    } catch {
      await restoreBrowserNotificationContext(previousRoute)
    }
  }

  const handleFirstInteraction = () => {
    unlockAudioContext()
    window.removeEventListener('click', handleFirstInteraction)
    window.removeEventListener('touchstart', handleFirstInteraction)
  }

  const handleSessionRevoked = () => {
    void ensureSessionValidation()
  }

  const handleReconnect = () => {
    const isNotificationCenterRoute =
      route.path === '/notifications' || route.path === '/account/notifications'

    if (skipNextReconnectCountsFetch) {
      skipNextReconnectCountsFetch = false
    } else {
      void notificationStore.fetchInitialCounts()
    }

    if (isNotificationCenterRoute) {
      void notificationStore.fetchHistory()
    }
  }

  const bootstrapAuthenticatedRuntime = () => {
    const authToken = localStorage.getItem('auth_token')
    if (!authToken) {
      bootstrappedAuthToken = null
      skipNextReconnectCountsFetch = false
      webPushDeliveryState = 'checking'
      return
    }

    const shouldBootstrapForToken = bootstrappedAuthToken !== authToken
    if (shouldBootstrapForToken) {
      skipNextReconnectCountsFetch = true
    }
    connect()

    if (!shouldBootstrapForToken) return
    bootstrappedAuthToken = authToken

    void notificationStore.fetchInitialCounts()
    void ensureSessionValidation()
    // Never prompt during bootstrap. If the user has already granted browser
    // permission but the subscription is missing, restore it automatically so
    // a permission/subscription split cannot silently suppress trade alerts.
    webPushDeliveryState = 'checking'
    void getWebPushStatus()
      .then((status) => {
        webPushDeliveryState = status.state === 'subscribed' ? 'subscribed' : 'fallback'
        if (
          isUnmounted ||
          status.state !== 'unsubscribed' ||
          localStorage.getItem('auth_token') !== authToken
        ) {
          return undefined
        }
        webPushDeliveryState = 'checking'
        return enableWebPushNotifications()
      })
      .then((status) => {
        if (status) {
          webPushDeliveryState = status.state === 'subscribed' ? 'subscribed' : 'fallback'
        }
      })
      .catch(() => {
        webPushDeliveryState = 'fallback'
      })
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

  watch(
    () => route.fullPath,
    () => {
      bootstrapAuthenticatedRuntime()
    },
  )

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
