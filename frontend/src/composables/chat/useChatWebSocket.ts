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
} from '../../utils/messengerStage6MediaRealtime'

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

    const TYPING_THROTTLE = 2000
    const TYPING_ACTIVITY_TIMEOUT_MS = 5000
    const lastTypingTimeByConversation = new Map<number, number>()

    const typingUsers = ref<Record<number, boolean>>({})
    const typingParticipantNames = ref<Record<number, Record<number, string>>>({})
    const uploadingParticipantNames = ref<Record<number, Record<number, string>>>({})
    const typingTimeouts = new Map<string, number>()

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
        handleActivityEvent({
            ...data,
            activity: 'typing',
            active: true,
        })
    }

    function handleActivityEvent(data: any) {
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

    function handleNewMessageEvent(data: any) {
        const senderId = Number(data?.sender_id)
        const arrivingConversationKey = getConversationKeyFromPayload(data)

        if (Number.isFinite(senderId) && arrivingConversationKey !== null) {
            clearConversationActivities(arrivingConversationKey, senderId)
        }

        const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null

        if (currentSelected !== null && arrivingConversationKey === currentSelected && data && typeof data.id === 'number') {
            const list = messages.value
            const alreadyExists = list.some((m: any) => m && m.id === data.id)
            if (!alreadyExists) {
                list.push(data)
                if (isUserAtBottom?.value) {
                    Promise.resolve().then(() => scrollToBottom())
                }
            }
            markAsRead()
        } else if (currentSelected !== null && arrivingConversationKey === currentSelected) {
            loadMessages(currentSelected, true).then(() => markAsRead())
        }

        let shouldReloadConversations = arrivingConversationKey === null
        if (arrivingConversationKey !== null) {
            const conv = conversations.value.find((c: any) => c && c.other_user_id === arrivingConversationKey)
            if (conv) {
                if (data?.created_at) conv.last_message_at = data.created_at
                if (data?.message_type) conv.last_message_type = data.message_type
                conv.last_message_content = getConversationPreviewContent(data)
                if (!(currentSelected !== null && arrivingConversationKey === currentSelected)) {
                    conv.unread_count = (conv.unread_count || 0) + 1
                }
            } else {
                shouldReloadConversations = true
            }
        }

        if (shouldReloadConversations) {
            scheduleConversationsReload()
        }
    }

    function handleReadEvent(data: any) {
        const roomConversationKey = resolveRoomConversationKey(data?.room_kind, data?.chat_id)
        if (roomConversationKey !== null) {
            const conversationKey = roomConversationKey
            if (selectedUserId.value === conversationKey) {
                const conv = conversations.value.find((item: any) => item && item.other_user_id === conversationKey)
                if (conv) {
                    conv.unread_count = 0
                }
            }
            return
        }

        if (selectedUserId.value && (data.reader_id === selectedUserId.value)) {
            const readerId = Number(data.reader_id)
            messages.value.forEach((m: any) => {
                if (m && m.receiver_id === readerId && !m.is_read) {
                    m.is_read = true
                }
            })
        }
    }

    function handleReactionEvent(data: any) {
        if (!data || typeof data.id !== 'number') {
            return
        }

        const messageIndex = messages.value.findIndex((message: any) => message && message.id === data.id)
        if (messageIndex === -1) {
            return
        }

        const existingMessage = messages.value[messageIndex]
        messages.value[messageIndex] = {
            ...existingMessage,
            reactions: Array.isArray(data.reactions)
                ? data.reactions
                    .map((reaction: any) => ({
                        emoji: typeof reaction?.emoji === 'string' ? reaction.emoji : '',
                        user_id: Number(reaction?.user_id),
                    }))
                    .filter((reaction: any) => reaction.emoji && Number.isFinite(reaction.user_id))
                : [],
        }
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
