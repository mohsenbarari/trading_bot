import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'
import { useWebSocket } from '../../composables/useWebSocket'

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

    function handleNewMessageEvent(data: any) {
        const senderId = data.sender_id;

        // Clear typing on message
        if (senderId) {
            typingUsers.value[senderId] = false;
            
            // Fix: Mark messages as read and reload immediately if sender matches selected chat
            if (selectedUserId.value && (senderId === selectedUserId.value)) {
                loadMessages(selectedUserId.value, true).then(() => {
                    markAsRead();
                    // Optional: scrollToBottom if near bottom
                });
            }
        } else {
            // Unlikely to lack sender_id, but just in case
        }

        // Always update conversations list (to show new message/count)
        loadConversations();
    }

    function handleReadEvent(data: any) {
        // If the current chat user read our messages, refresh
        if (selectedUserId.value && (data.reader_id === selectedUserId.value)) {
            loadMessages(selectedUserId.value, true)
        }
    }

    function setupWebSocketListeners() {
        ws.on('chat:message', handleNewMessageEvent)
        ws.on('chat:typing', handleTypingEvent)
        ws.on('chat:read', handleReadEvent)
        ws.on('message', handleNewMessageEvent)
        
        // Keep legacy listeners just in case backend still broadcasts under old names
        ws.on('chat-message', handleNewMessageEvent)
        ws.on('chat-typing', handleTypingEvent)
        ws.on('chat-read', handleReadEvent)
    }

    function teardownWebSocketListeners() {
        ws.off('chat:message', handleNewMessageEvent)
        ws.off('chat:typing', handleTypingEvent)
        ws.off('chat:read', handleReadEvent)
        ws.off('message', handleNewMessageEvent)
        
        ws.off('chat-message', handleNewMessageEvent)
        ws.off('chat-typing', handleTypingEvent)
        ws.off('chat-read', handleReadEvent)
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
