import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'

export interface UseChatWebSocketOptions {
    selectedUserId: Ref<number | null>
    messageInput: Ref<string>
    apiFetch: (endpoint: string, options?: any) => Promise<any>
    loadConversations: () => Promise<void>
    loadMessages: (userId: number, preserveOffset?: boolean, jumpToMsgId?: number) => Promise<void>
    scrollToBottom: () => void
    markAsRead: () => Promise<void>
}

export function useChatWebSocket(options: UseChatWebSocketOptions) {
    const {
        selectedUserId,
        messageInput,
        apiFetch,
        loadConversations,
        loadMessages,
        scrollToBottom,
        markAsRead
    } = options

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

    function handleTypingEvent(e: Event) {
        const data = (e as CustomEvent).detail;
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

    function handleNewMessageEvent(e: Event) {
        const notif = (e as CustomEvent).detail
        const senderId = notif.sender_id

        // Clear typing on message
        if (senderId) {
            typingUsers.value[senderId] = false;
        }

        // Always update conversations list (to show new message/count)
        loadConversations();

        // Refresh if chat with sender
        if (selectedUserId.value && (senderId === selectedUserId.value)) {
            loadMessages(selectedUserId.value, true)
            markAsRead()
        }
    }

    function handleReadEvent(e: Event) {
        const data = (e as CustomEvent).detail
        // If the current chat user read our messages, refresh
        if (selectedUserId.value && (data.reader_id === selectedUserId.value)) {
            loadMessages(selectedUserId.value, true)
        }
    }

    function setupWebSocketListeners() {
        window.addEventListener('chat-notification', handleNewMessageEvent)
        window.addEventListener('chat-message', handleNewMessageEvent)
        window.addEventListener('chat-typing', handleTypingEvent)
        window.addEventListener('chat-read', handleReadEvent)
    }

    function teardownWebSocketListeners() {
        window.removeEventListener('chat-message', handleNewMessageEvent)
        window.removeEventListener('chat-notification', handleNewMessageEvent)
        window.removeEventListener('chat-typing', handleTypingEvent)
        window.removeEventListener('chat-read', handleReadEvent)
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
