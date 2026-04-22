import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'
import { useWebSocket } from '../../composables/useWebSocket'

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

    function handleNewMessageEvent(data: any) {
        const senderId = data.sender_id;

        // Clear typing on message
        if (senderId) {
            typingUsers.value[senderId] = false;
        }

        const currentSelected = selectedUserId.value ? Number(selectedUserId.value) : null;
        const arrivingSender = senderId != null ? Number(senderId) : null;

        // Append directly to messages if this chat is open. The backend
        // publishes a fully serialized MessageRead so we avoid a 200-msg
        // refetch on every incoming message.
        if (currentSelected !== null && arrivingSender === currentSelected && data && typeof data.id === 'number') {
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
        } else if (currentSelected !== null && arrivingSender === currentSelected) {
            // Payload missing fields — fall back to the previous full reload.
            loadMessages(currentSelected, true).then(() => markAsRead())
        }

        // Patch conversation list entry in-place so the preview/unread
        // update is instant, and still schedule a debounced reload as
        // a convergence safety net.
        if (arrivingSender !== null) {
            const conv = conversations.value.find((c: any) => c && c.other_user_id === arrivingSender)
            if (conv) {
                if (data?.created_at) conv.last_message_at = data.created_at
                if (data?.content !== undefined) conv.last_message_preview = data.content
                if (data?.message_type) conv.last_message_type = data.message_type
                if (!(currentSelected !== null && arrivingSender === currentSelected)) {
                    conv.unread_count = (conv.unread_count || 0) + 1
                }
            }
        }

        scheduleConversationsReload();
    }

    function handleReadEvent(data: any) {
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

    function setupWebSocketListeners() {
        ws.on('chat:message', handleNewMessageEvent)
        ws.on('chat:typing', handleTypingEvent)
        ws.on('chat:read', handleReadEvent)
    }

    function teardownWebSocketListeners() {
        ws.off('chat:message', handleNewMessageEvent)
        ws.off('chat:typing', handleTypingEvent)
        ws.off('chat:read', handleReadEvent)
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
