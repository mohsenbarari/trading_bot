import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'
import { useWebSocket } from '../../composables/useWebSocket'
import {
    buildChatActivityBody,
    buildChatActivityEndpoint,
    resolveRoomConversationKey,
} from '../../utils/chatRoomRouting'
import { getConversationPreviewText } from '../../utils/chatMessagePreview'
import {
    buildMessengerActivityLabel,
    normalizeMessengerRealtimeActivityPayload,
    resolveMessengerRealtimeConversationKey,
} from '../../utils/chatRealtimeMediaPolicy'
import { createChatEventGateway } from '../../services/chat/chatEventGateway'
import { useChatSessionStore } from '../../stores/chat/session'
import { useConversationsStore } from '../../stores/chat/conversations'
import { useMessagesStore } from '../../stores/chat/messages'

export interface UseChatWebSocketOptions {
    selectedUserId: Ref<number | null>
    messageInput: Ref<string>
    messages: Ref<any[]>
    conversations: Ref<any[]>
    apiFetch: (endpoint: string, options?: any) => Promise<any>
    loadConversations: () => Promise<void>
    loadMessages: (userId: number, preserveOffset?: boolean, jumpToMsgId?: number) => Promise<void>
    scrollToBottom: () => void
    markAsRead: () => Promise<void>
    isUserAtBottom?: Ref<boolean>
}

export function useChatWebSocket(options: UseChatWebSocketOptions) {
    const {
        selectedUserId,
        messages,
        conversations,
        apiFetch,
        loadConversations,
        loadMessages,
        scrollToBottom,
        markAsRead,
        isUserAtBottom,
    } = options

    const ws = useWebSocket()
    const chatGateway = createChatEventGateway({
        session: useChatSessionStore(),
        conversations: useConversationsStore(),
        messages: useMessagesStore(),
    })

    const TYPING_THROTTLE = 2000
    const TYPING_ACTIVITY_TIMEOUT_MS = 5000
    const lastTypingTimeByConversation = new Map<number, number>()

    const typingUsers = ref<Record<number, boolean>>({})
    const typingParticipantNames = ref<Record<number, Record<number, string>>>({})
    const uploadingParticipantNames = ref<Record<number, Record<number, string>>>({})
    const typingTimeouts = new Map<string, number>()
    const pendingRealtimeEvents: Array<
        | { kind: 'message'; data: any }
        | { kind: 'read'; data: any }
        | { kind: 'reaction'; data: any }
    > = []
    let flushScheduled = false
    let isUnmounted = false

    const isTyping = computed(() => selectedUserId.value ? !!typingUsers.value[selectedUserId.value] : false)
    const activityTextByConversation = computed<Record<number, string>>(() => {
        const result: Record<number, string> = {}
        const conversationKeys = new Set<number>([
            ...Object.keys(typingParticipantNames.value).map(Number),
            ...Object.keys(uploadingParticipantNames.value).map(Number),
        ])

        conversationKeys.forEach((conversationKey) => {
            if (!Number.isFinite(conversationKey)) return

            const uploadNames = Object.values(uploadingParticipantNames.value[conversationKey] || {})
            const typingNames = Object.values(typingParticipantNames.value[conversationKey] || {})

            if (uploadNames.length > 0) {
                result[conversationKey] = buildMessengerActivityLabel(conversationKey, 'uploading_file', uploadNames)
                return
            }

            if (typingNames.length > 0) {
                result[conversationKey] = buildMessengerActivityLabel(conversationKey, 'typing', typingNames)
            }
        })

        return result
    })

    function getConversationKeyFromPayload(data: any): number | null {
        return resolveMessengerRealtimeConversationKey(data)
    }

    function setConversationParticipantName(
        target: typeof typingParticipantNames | typeof uploadingParticipantNames,
        conversationKey: number,
        senderId: number,
        senderName: string,
    ) {
        const nextConversationState = {
            ...(target.value[conversationKey] || {}),
            [senderId]: senderName,
        }

        target.value = {
            ...target.value,
            [conversationKey]: nextConversationState,
        }
    }

    function clearConversationParticipantName(
        target: typeof typingParticipantNames | typeof uploadingParticipantNames,
        conversationKey: number,
        senderId: number,
    ) {
        const conversationState = target.value[conversationKey]
        if (!conversationState) return

        const nextConversationState = { ...conversationState }
        delete nextConversationState[senderId]

        const nextState = { ...target.value }
        if (Object.keys(nextConversationState).length > 0) {
            nextState[conversationKey] = nextConversationState
        } else {
            delete nextState[conversationKey]
        }

        target.value = nextState
    }

    function syncDirectTypingState(conversationKey: number) {
        if (conversationKey <= 0) return
        typingUsers.value[conversationKey] = Object.keys(typingParticipantNames.value[conversationKey] || {}).length > 0
    }

    function removeTypingActivity(conversationKey: number, senderId: number) {
        const timeoutKey = `${conversationKey}:${senderId}`
        const existingTimeout = typingTimeouts.get(timeoutKey)
        if (existingTimeout) {
            window.clearTimeout(existingTimeout)
            typingTimeouts.delete(timeoutKey)
        }

        clearConversationParticipantName(typingParticipantNames, conversationKey, senderId)
        syncDirectTypingState(conversationKey)
    }

    function upsertTypingActivity(conversationKey: number, senderId: number, senderName: string) {
        setConversationParticipantName(typingParticipantNames, conversationKey, senderId, senderName)
        syncDirectTypingState(conversationKey)

        const timeoutKey = `${conversationKey}:${senderId}`
        const existingTimeout = typingTimeouts.get(timeoutKey)
        if (existingTimeout) {
            window.clearTimeout(existingTimeout)
        }

        typingTimeouts.set(timeoutKey, window.setTimeout(() => {
            typingTimeouts.delete(timeoutKey)
            removeTypingActivity(conversationKey, senderId)
        }, TYPING_ACTIVITY_TIMEOUT_MS))
    }

    function setUploadActivity(conversationKey: number, senderId: number, senderName: string, active: boolean) {
        if (active) {
            setConversationParticipantName(uploadingParticipantNames, conversationKey, senderId, senderName)
            return
        }
        clearConversationParticipantName(uploadingParticipantNames, conversationKey, senderId)
    }

    function clearConversationActivities(conversationKey: number, senderId: number) {
        removeTypingActivity(conversationKey, senderId)
        clearConversationParticipantName(uploadingParticipantNames, conversationKey, senderId)
    }

    async function sendTypingSignal() {
        const conversationKey = selectedUserId.value
        if (!conversationKey) return

        const now = Date.now()
        const lastTypingTime = lastTypingTimeByConversation.get(conversationKey) ?? 0
        if (now - lastTypingTime < TYPING_THROTTLE) return
        lastTypingTimeByConversation.set(conversationKey, now)

        try {
            await apiFetch(buildChatActivityEndpoint(conversationKey), {
                method: 'POST',
                body: JSON.stringify(buildChatActivityBody(conversationKey, { activity: 'typing', active: true })),
            })
        } catch (e) {
            console.error('Typing signal failed', e)
        }
    }

    const handleTypingWrapper = () => {
        sendTypingSignal()
    }

    function handleTypingEvent(data: any) {
        chatGateway.dispatch('chat:typing', data)
        handleActivityEvent({
            ...data,
            activity: 'typing',
            active: true,
        })
    }

    function handleActivityEvent(data: any) {
        chatGateway.dispatch('chat:activity', data)
        const activityPayload = normalizeMessengerRealtimeActivityPayload(data)
        if (!activityPayload) {
            return
        }

        const { conversationKey, senderId, senderName, activity, active } = activityPayload

        if (activity === 'typing') {
            if (active) {
                upsertTypingActivity(conversationKey, senderId, senderName)
            } else {
                removeTypingActivity(conversationKey, senderId)
            }
            return
        }

        setUploadActivity(conversationKey, senderId, senderName, active)
    }

    let convReloadTimer: number | null = null
    function scheduleConversationsReload() {
        if (convReloadTimer !== null) return
        convReloadTimer = window.setTimeout(() => {
            convReloadTimer = null
            loadConversations()
        }, 400)
    }

    function getConversationPreviewContent(data: any) {
        if (data?.is_deleted) return 'پیام حذف شد'
        return getConversationPreviewText(data?.message_type, data?.content)
    }

    function getEventTimestamp(value: unknown) {
        if (typeof value !== 'string' && typeof value !== 'number') return null
        const timestamp = new Date(value).getTime()
        return Number.isFinite(timestamp) ? timestamp : null
    }

    function isSameOrNewerEvent(candidateAt?: string | null, currentAt?: string | null) {
        const candidateTimestamp = getEventTimestamp(candidateAt)
        const currentTimestamp = getEventTimestamp(currentAt)
        if (candidateTimestamp === null || currentTimestamp === null) return true
        return candidateTimestamp >= currentTimestamp
    }

    function normalizeReactionList(reactions: any) {
        return Array.isArray(reactions)
            ? reactions
                .map((reaction: any) => ({
                    emoji: typeof reaction?.emoji === 'string' ? reaction.emoji : '',
                    user_id: Number(reaction?.user_id),
                }))
                .filter((reaction: any) => reaction.emoji && Number.isFinite(reaction.user_id))
            : []
    }

    function queueConversationPatch(
        conversationPatches: Map<number, {
            last_message_at?: string
            last_message_type?: string
            last_message_content?: string
            unreadDelta: number
            resetUnread: boolean
        }>,
        conversationKey: number,
        patch: Partial<{
            last_message_at: string
            last_message_type: string
            last_message_content: string
            unreadDelta: number
            resetUnread: boolean
        }>
    ) {
        const currentPatch = conversationPatches.get(conversationKey) || {
            unreadDelta: 0,
            resetUnread: false,
        }

        const canReplacePreview = patch.last_message_at
            ? isSameOrNewerEvent(patch.last_message_at, currentPatch.last_message_at)
            : false

        conversationPatches.set(conversationKey, {
            ...currentPatch,
            ...(canReplacePreview ? patch : {}),
            unreadDelta: currentPatch.unreadDelta + (patch.unreadDelta || 0),
            resetUnread: currentPatch.resetUnread || patch.resetUnread === true,
        })
    }

    function collectNewMessageEvent(
        data: any,
        flushState: {
            shouldScrollToBottom: boolean
            shouldMarkAsRead: boolean
            shouldReloadSelectedMessages: boolean
            appendedMessages: any[]
            knownMessageIds: Set<number>
            conversationPatches: Map<number, {
                last_message_at?: string
                last_message_type?: string
                last_message_content?: string
                unreadDelta: number
                resetUnread: boolean
            }>
            shouldReloadConversations: boolean
        }
    ) {
        const senderId = Number(data?.sender_id)
        const arrivingConversationKey = getConversationKeyFromPayload(data)

        if (Number.isFinite(senderId) && arrivingConversationKey !== null) {
            clearConversationActivities(arrivingConversationKey, senderId)
        }

        const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null

        if (currentSelected !== null && arrivingConversationKey === currentSelected && data && typeof data.id === 'number') {
            const alreadyExists = flushState.knownMessageIds.has(data.id)
            if (!alreadyExists) {
                flushState.knownMessageIds.add(data.id)
                flushState.appendedMessages.push(data)
                flushState.shouldScrollToBottom = flushState.shouldScrollToBottom || !!isUserAtBottom?.value
            }
            flushState.shouldMarkAsRead = true
        } else if (currentSelected !== null && arrivingConversationKey === currentSelected) {
            flushState.shouldReloadSelectedMessages = true
        }

        let shouldReloadConversations = arrivingConversationKey === null
        if (arrivingConversationKey !== null) {
            const conv = conversations.value.find((c: any) => c && c.other_user_id === arrivingConversationKey)
            if (conv) {
                queueConversationPatch(flushState.conversationPatches, arrivingConversationKey, {
                    last_message_at: data?.created_at,
                    last_message_type: data?.message_type,
                    last_message_content: getConversationPreviewContent(data),
                    unreadDelta: !(currentSelected !== null && arrivingConversationKey === currentSelected) ? 1 : 0,
                })
            } else {
                shouldReloadConversations = true
            }
        }

        if (shouldReloadConversations) {
            flushState.shouldReloadConversations = true
        }
    }

    function collectReadEvent(
        data: any,
        flushState: {
            directReaderIdsToMark: Set<number>
            conversationPatches: Map<number, {
                last_message_at?: string
                last_message_type?: string
                last_message_content?: string
                unreadDelta: number
                resetUnread: boolean
            }>
        }
    ) {
        const roomConversationKey = resolveRoomConversationKey(data?.room_kind, data?.chat_id)
        if (roomConversationKey !== null) {
            const conversationKey = roomConversationKey
            if (selectedUserId.value === conversationKey) {
                queueConversationPatch(flushState.conversationPatches, conversationKey, { resetUnread: true })
            }
            return
        }

        if (selectedUserId.value && (data.reader_id === selectedUserId.value)) {
            flushState.directReaderIdsToMark.add(Number(data.reader_id))
        }
    }

    function collectReactionEvent(data: any, reactionUpdates: Map<number, any[]>) {
        if (!data || typeof data.id !== 'number') {
            return
        }
        reactionUpdates.set(data.id, normalizeReactionList(data.reactions))
    }

    function flushRealtimeEvents() {
        flushScheduled = false
        if (isUnmounted) {
            pendingRealtimeEvents.length = 0
            return
        }

        const events = pendingRealtimeEvents.splice(0, pendingRealtimeEvents.length)
        const flushState = {
            shouldScrollToBottom: false,
            shouldMarkAsRead: false,
            shouldReloadSelectedMessages: false,
            appendedMessages: [] as any[],
            knownMessageIds: new Set(messages.value.map((message: any) => message?.id).filter((id: any) => typeof id === 'number')),
            conversationPatches: new Map<number, {
                last_message_at?: string
                last_message_type?: string
                last_message_content?: string
                unreadDelta: number
                resetUnread: boolean
            }>(),
            directReaderIdsToMark: new Set<number>(),
            reactionUpdates: new Map<number, any[]>(),
            shouldReloadConversations: false,
        }

        for (const event of events) {
            if (event.kind === 'message') {
                collectNewMessageEvent(event.data, flushState)
                continue
            }
            if (event.kind === 'read') {
                collectReadEvent(event.data, flushState)
                continue
            }
            collectReactionEvent(event.data, flushState.reactionUpdates)
        }

        if (flushState.appendedMessages.length > 0) {
            messages.value = [...messages.value, ...flushState.appendedMessages]
        }

        if (flushState.directReaderIdsToMark.size > 0 || flushState.reactionUpdates.size > 0) {
            messages.value = messages.value.map((message: any) => {
                if (!message) return message

                let nextMessage = message
                if (
                    flushState.directReaderIdsToMark.size > 0
                    && Number.isFinite(Number(message.receiver_id))
                    && flushState.directReaderIdsToMark.has(Number(message.receiver_id))
                    && !message.is_read
                ) {
                    nextMessage = { ...nextMessage, is_read: true }
                }

                const nextReactions = flushState.reactionUpdates.get(message.id)
                if (nextReactions) {
                    nextMessage = { ...nextMessage, reactions: nextReactions }
                }

                return nextMessage
            })
        }

        if (flushState.conversationPatches.size > 0) {
            conversations.value = conversations.value.map((conversation: any) => {
                if (!conversation) return conversation
                const conversationKey = Number(conversation.other_user_id)
                const patch = flushState.conversationPatches.get(conversationKey)
                if (!patch) return conversation

                const nextUnreadCount = patch.resetUnread
                    ? 0
                    : Math.max(0, Number(conversation.unread_count || 0) + patch.unreadDelta)

                const canApplyPreview = patch.last_message_at
                    ? isSameOrNewerEvent(patch.last_message_at, conversation.last_message_at)
                    : false

                return {
                    ...conversation,
                    last_message_at: canApplyPreview ? patch.last_message_at : conversation.last_message_at,
                    last_message_type: canApplyPreview ? patch.last_message_type : conversation.last_message_type,
                    last_message_content: canApplyPreview ? patch.last_message_content : conversation.last_message_content,
                    unread_count: nextUnreadCount,
                }
            })
        }

        if (flushState.shouldReloadConversations) {
            scheduleConversationsReload()
        }

        const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null
        if (flushState.shouldReloadSelectedMessages && currentSelected !== null) {
            void loadMessages(currentSelected, true).then(() => markAsRead())
        } else if (flushState.shouldMarkAsRead) {
            void markAsRead()
        }

        if (flushState.shouldScrollToBottom) {
            Promise.resolve().then(() => {
                if (!isUnmounted) {
                    scrollToBottom()
                }
            })
        }
    }

    function enqueueRealtimeEvent(event: (typeof pendingRealtimeEvents)[number]) {
        pendingRealtimeEvents.push(event)
        if (flushScheduled) return
        flushScheduled = true
        Promise.resolve().then(flushRealtimeEvents)
    }

    function handleNewMessageEvent(data: any) {
        chatGateway.dispatch('chat:message', data)
        enqueueRealtimeEvent({ kind: 'message', data })
    }

    function handleReadEvent(data: any) {
        chatGateway.dispatch('chat:read', data)
        enqueueRealtimeEvent({ kind: 'read', data })
    }

    function handleReactionEvent(data: any) {
        chatGateway.dispatch('chat:reaction', data)
        enqueueRealtimeEvent({ kind: 'reaction', data })
    }

    function setupWebSocketListeners() {
        ws.on('chat:message', handleNewMessageEvent)
        ws.on('chat:typing', handleTypingEvent)
        ws.on('chat:activity', handleActivityEvent)
        ws.on('chat:read', handleReadEvent)
        ws.on('chat:reaction', handleReactionEvent)
    }

    function teardownWebSocketListeners() {
        ws.off('chat:message', handleNewMessageEvent)
        ws.off('chat:typing', handleTypingEvent)
        ws.off('chat:activity', handleActivityEvent)
        ws.off('chat:read', handleReadEvent)
        ws.off('chat:reaction', handleReactionEvent)
    }

    onMounted(() => {
        setupWebSocketListeners()
    })

    onUnmounted(() => {
        isUnmounted = true
        flushScheduled = false
        pendingRealtimeEvents.length = 0
        typingTimeouts.forEach((timeoutId) => {
            window.clearTimeout(timeoutId)
        })
        typingTimeouts.clear()
        teardownWebSocketListeners()
    })

    return {
        typingUsers,
        activityTextByConversation,
        isTyping,
        handleTypingWrapper,
        sendTypingSignal,
    }
}
