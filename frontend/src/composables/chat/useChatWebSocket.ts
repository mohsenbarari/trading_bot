import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'
import { useWebSocket } from '../../composables/useWebSocket'
import { resolveRoomConversationKey } from '../../utils/chatRoomRouting'
import { getConversationPreviewText } from '../../utils/chatMessagePreview'

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
        messageInput,
        messages,
        conversations,
        apiFetch,
        loadConversations,
        loadMessages,
        scrollToBottom,
        markAsRead,
        isUserAtBottom
    } = options

    const ws = useWebSocket()

    const TYPING_THROTTLE = 2000
    let lastTypingTime = 0

    const typingUsers = ref<Record<number, boolean>>({})
    const typingTimeouts = ref<Record<number, number>>({})
    const isTyping = computed(() => selectedUserId.value ? !!typingUsers.value[selectedUserId.value] : false)

    function getConversationKeyFromPayload(data: any): number | null {
        const roomConversationKey = resolveRoomConversationKey(data?.room_kind, data?.chat_id)
        if (roomConversationKey !== null) {
            return roomConversationKey
        }

        const senderId = Number(data?.sender_id)
        return Number.isFinite(senderId) ? senderId : null
    }

    async function sendTypingSignal() {
        if (!messageInput.value) return;
        const now = Date.now();
        if (now - lastTypingTime < TYPING_THROTTLE) return;
        lastTypingTime = now;

        if (!selectedUserId.value) return;

        try {
            await apiFetch('/chat/typing', {
                method: 'POST',
                body: JSON.stringify({ receiver_id: selectedUserId.value })
            });
        } catch (e) {
            console.error('Typing signal failed', e);
        }
    }

    const handleTypingWrapper = () => {
        sendTypingSignal();
    };

    function handleTypingEvent(data: any) {
        const senderId = data.sender_id;
        if (senderId) {
            typingUsers.value[senderId] = true;

            if (typingTimeouts.value[senderId]) clearTimeout(typingTimeouts.value[senderId]);

            // Use window.setTimeout to ensure number return type
            typingTimeouts.value[senderId] = window.setTimeout(() => {
                typingUsers.value[senderId] = false;
            }, 5000);
        }
    }

    // Debounced full conversation list reload so bursts of websocket
    // events don't trigger many back-to-back /conversations fetches.
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
        const senderId = data.sender_id;

        // Clear typing on message
        if (senderId) {
            typingUsers.value[senderId] = false;
        }

        const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null;
        const arrivingConversationKey = getConversationKeyFromPayload(data)

        // Append directly to messages if this chat is open. The backend
        // publishes a fully serialized MessageRead so we avoid a 200-msg
        // refetch on every incoming message.
        if (currentSelected !== null && arrivingConversationKey === currentSelected && data && typeof data.id === 'number') {
            const list = messages.value
            const alreadyExists = list.some((m: any) => m && m.id === data.id)
            if (!alreadyExists) {
                list.push(data)
                if (isUserAtBottom?.value) {
                    Promise.resolve().then(() => scrollToBottom())
                }
            }
            // Mark as read quickly since chat is open.
            markAsRead();
        } else if (currentSelected !== null && arrivingConversationKey === currentSelected) {
            // Payload missing fields — fall back to the previous full reload.
            loadMessages(currentSelected, true).then(() => markAsRead())
        }

        // Patch conversation list entry in-place so the preview/unread
        // update is instant, and still schedule a debounced reload as
        // a convergence safety net.
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

        // If the current chat user read our messages, patch in-place
        // instead of refetching all 200 messages.
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
        ws.on('chat:read', handleReadEvent)
        ws.on('chat:reaction', handleReactionEvent)
    }

    function teardownWebSocketListeners() {
        ws.off('chat:message', handleNewMessageEvent)
        ws.off('chat:typing', handleTypingEvent)
        ws.off('chat:read', handleReadEvent)
        ws.off('chat:reaction', handleReactionEvent)
    }

    onMounted(() => {
        setupWebSocketListeners()
    })

    onUnmounted(() => {
        teardownWebSocketListeners()
    })

    return {
        typingUsers,
        isTyping,
        handleTypingWrapper,
        sendTypingSignal
    }
}
