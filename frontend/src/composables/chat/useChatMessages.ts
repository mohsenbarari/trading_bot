import { ref, type Ref, nextTick } from 'vue'
import { apiFetchJson } from '../../utils/auth'
import type { Conversation, Message, StickerPack } from '../../types/chat'
import { useNotificationStore } from '../../stores/notifications'

export interface UseChatMessagesOptions {
    apiBaseUrl: string
    jwtToken: string | null
    currentUserId: number
    selectedUserId: Ref<number | null>
    messages: Ref<Message[]>
    conversations: Ref<Conversation[]>
    error: Ref<string>
    isLoadingMessages: Ref<boolean>
    isSending: Ref<boolean>
    unreadNewMessagesCount: Ref<number>
    isUserAtBottom: Ref<boolean>
    isViewingReply: Ref<boolean>
    targetUserStatus: Ref<string>
    messageInput: Ref<string>
    messageInputRef: Ref<HTMLTextAreaElement | null>
    editingMessage: Ref<Message | null>
    replyingToMessage: Ref<Message | null>
    swipedMessageId: Ref<number | null>
    isMobile: Ref<boolean>
    stickerPacks: Ref<StickerPack[]>
    showStickerPicker: Ref<boolean>
    scrollToBottom: () => void
    scrollToUnreadOrBottom: () => void
    forceScrollToBottom: () => void
    adjustTextareaHeight: () => void
}

export function useChatMessages(options: UseChatMessagesOptions) {
    const {
        apiBaseUrl,
        jwtToken,
        currentUserId,
        selectedUserId,
        messages,
        conversations,
        error,
        isLoadingMessages,
        isSending,
        unreadNewMessagesCount,
        isUserAtBottom,
        isViewingReply,
        targetUserStatus,
        messageInput,
        messageInputRef,
        editingMessage,
        replyingToMessage,
        swipedMessageId,
        isMobile,
        stickerPacks,
        showStickerPicker,
        scrollToBottom,
        scrollToUnreadOrBottom,
        forceScrollToBottom,
        adjustTextareaHeight
    } = options

    const notificationStore = useNotificationStore()

    let pollTimer: number | null = null
    const POLL_INTERVAL = 30000
    let statusPollTimer: number | null = null

    function formatLastSeen(date: Date): string {
        const now = new Date()
        const diffSeconds = Math.floor((now.getTime() - date.getTime()) / 1000)

        if (diffSeconds < 180) return 'آنلاین'
        if (diffSeconds < 3600) {
            const mins = Math.floor(diffSeconds / 60)
            return `آخرین بازدید ${mins} دقیقه پیش`
        }

        const isToday = now.getDate() === date.getDate() &&
            now.getMonth() === date.getMonth() &&
            now.getFullYear() === date.getFullYear()

        const hours = date.getHours().toString().padStart(2, '0')
        const mins = date.getMinutes().toString().padStart(2, '0')

        if (isToday) return `آخرین بازدید امروز ${hours}:${mins}`

        const yesterday = new Date(now)
        yesterday.setDate(yesterday.getDate() - 1)
        const isYesterday = yesterday.getDate() === date.getDate() &&
            yesterday.getMonth() === date.getMonth() &&
            yesterday.getFullYear() === date.getFullYear()

        if (isYesterday) return `آخرین بازدید دیروز ${hours}:${mins}`

        return `آخرین بازدید ${date.toLocaleDateString('fa-IR')}`
    }

    async function apiFetch(endpoint: string, fetchOptions: RequestInit = {}) {
        return await apiFetchJson(`/api${endpoint}`, fetchOptions)
    }

    async function loadConversations() {
        try {
            conversations.value = await apiFetch('/chat/conversations')
        } catch (e: any) {
            error.value = e.message
        }
    }

    async function loadMessages(userId: number, silent = false, aroundId?: number) {
        if (!silent) isLoadingMessages.value = true
        try {
            let url = `/chat/messages/${userId}?limit=200&_t=${Date.now()}`

            if (aroundId) {
                url = `/chat/messages/${userId}?limit=50&around_id=${aroundId}&_t=${Date.now()}`
                if (!silent) messages.value = []
            }

            const loadedMessages = await apiFetch(url)

            if (aroundId) {
                messages.value = loadedMessages
                isLoadingMessages.value = false
                return
            }

            if (silent) {
                const lastOldMsg = messages.value[messages.value.length - 1]
                const lastNewMsg = loadedMessages[loadedMessages.length - 1]
                const isNewMessage = lastNewMsg && (!lastOldMsg || lastNewMsg.id !== lastOldMsg.id)
                const oldLength = messages.value.length

                const tempParams = messages.value.filter(m => m.id < 0)
                messages.value = [...loadedMessages, ...tempParams]

                if (isNewMessage) {
                    if (lastNewMsg.sender_id !== currentUserId) {
                        if (isUserAtBottom.value && !isViewingReply.value) {
                            await nextTick()
                            scrollToBottom()
                            markAsRead()
                        } else {
                            const diff = loadedMessages.length - oldLength
                            unreadNewMessagesCount.value += (diff > 0 ? diff : 1)
                        }
                    } else if (lastNewMsg.sender_id === currentUserId) {
                        if (isUserAtBottom.value && !isViewingReply.value) {
                            await nextTick()
                            scrollToBottom()
                        }
                    }
                }
            } else {
                messages.value = loadedMessages
                unreadNewMessagesCount.value = 0
                isLoadingMessages.value = false
                await nextTick()
                scrollToUnreadOrBottom()
                markAsRead()
            }
        } catch (e: any) {
            if (!silent) error.value = e.message
            if (!silent) isLoadingMessages.value = false
        } finally {
            if (!silent && isLoadingMessages.value) isLoadingMessages.value = false
        }
    }

    async function markAsRead() {
        if (!selectedUserId.value) return
        try {
            await apiFetch(`/chat/read/${selectedUserId.value}`, { method: 'POST' })
            const conv = conversations.value.find(c => c.other_user_id === selectedUserId.value)
            if (conv) {
                conv.unread_count = 0
                // Refresh global total unread count
                notificationStore.fetchInitialCounts()
            }
        } catch (e) {
            console.error('Failed to mark as read', e)
        }
    }

    async function sendMediaMessage(type: 'image' | 'video' | 'voice' | 'sticker', content: string, localBlobUrl?: string) {
        if (!selectedUserId.value) return

        isSending.value = true
        try {
            const newMsg = await apiFetch('/chat/send', {
                method: 'POST',
                body: JSON.stringify({
                    receiver_id: selectedUserId.value,
                    content: content,
                    message_type: type
                })
            })
            if (localBlobUrl) {
                newMsg.local_blob_url = localBlobUrl
            }
            messages.value.push(newMsg)
            showStickerPicker.value = false
            scrollToBottom()
        } catch (e: any) {
            error.value = e.message
        } finally {
            isSending.value = false
        }
    }

    const sendMessage = async () => {
        if (!messageInput.value.trim() && !editingMessage.value) return;

        if (editingMessage.value) {
            const msgToEdit = editingMessage.value
            try {
                const updatedMsg = await apiFetch(`/chat/messages/${msgToEdit.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({ content: messageInput.value })
                });
                const index = messages.value.findIndex(m => m.id === msgToEdit.id);
                if (index !== -1) {
                    messages.value[index] = updatedMsg;
                }
                cancelEdit();
            } catch (err) {
                console.error('Failed to edit message:', err);
                alert('خطا در ویرایش پیام');
            }
            return;
        }

        if (!selectedUserId.value) return;
        const content = messageInput.value;
        const replyTo = replyingToMessage.value;

        const tempId = -Date.now();
        const tempMsg: Message = {
            id: tempId,
            sender_id: currentUserId,
            receiver_id: selectedUserId.value,
            content: content,
            message_type: 'text',
            is_read: false,
            created_at: new Date().toISOString(),
            is_sending: true,
            reply_to_message: replyTo ? {
                id: replyTo.id,
                sender_id: replyTo.sender_id,
                content: replyTo.content,
                message_type: replyTo.message_type
            } : undefined
        };

        messages.value.push(tempMsg);

        messageInput.value = '';
        replyingToMessage.value = null;
        if (isMobile.value) swipedMessageId.value = null;
        adjustTextareaHeight();

        nextTick(() => {
            if (replyTo) forceScrollToBottom();
            else scrollToBottom();
        });

        try {
            const body: Record<string, any> = {
                receiver_id: selectedUserId.value,
                content: content,
                message_type: 'text'
            };
            if (replyTo) body.reply_to_message_id = replyTo.id;

            const serverMsg = await apiFetch('/chat/send', {
                method: 'POST',
                body: JSON.stringify(body)
            });

            const idx = messages.value.findIndex(m => m.id === tempId);
            if (idx !== -1) {
                messages.value[idx] = serverMsg;
            }

            nextTick(() => messageInputRef.value?.focus());
        } catch (err) {
            console.error('Failed to send message:', err);
            const idx = messages.value.findIndex(m => m.id === tempId);
            if (idx !== -1 && messages.value[idx]) {
                messages.value[idx].is_sending = false;
                messages.value[idx].is_error = true;
            }
        }
    };

    const cancelEdit = () => {
        editingMessage.value = null;
        messageInput.value = '';
        adjustTextareaHeight();
    };

    const handleReply = (msg: Message) => {
        replyingToMessage.value = msg
        nextTick(() => {
            messageInputRef.value?.focus()
        })
    }

    const cancelReply = () => {
        replyingToMessage.value = null
        if (isMobile.value) {
            swipedMessageId.value = null
        }
    }

    async function fetchTargetUserStatus(userId: number) {
        try {
            const userData = await apiFetch(`/users-public/${userId}`)
            if (!userData) return

            if (userData.last_seen_at) {
                const serverStr = userData.last_seen_at.endsWith('Z') ? userData.last_seen_at : userData.last_seen_at + 'Z';
                const serverDate = new Date(serverStr)
                targetUserStatus.value = formatLastSeen(serverDate)
            } else {
                targetUserStatus.value = 'آخرین بازدید خیلی وقت پیش'
            }
        } catch (e) {
            console.error("Error fetching status", e)
        }
    }

    async function poll() {
        await loadConversations()
        if (selectedUserId.value) {
            await loadMessages(selectedUserId.value, true)
        }
    }

    function startPolling() {
        stopPolling()
        pollTimer = window.setInterval(poll, POLL_INTERVAL)
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer)
            pollTimer = null
        }
    }

    function startStatusPolling(userId: number) {
        stopStatusPolling()
        fetchTargetUserStatus(userId)
        statusPollTimer = window.setInterval(() => fetchTargetUserStatus(userId), 30000)
    }

    function stopStatusPolling() {
        if (statusPollTimer) {
            clearInterval(statusPollTimer)
            statusPollTimer = null
        }
    }

    async function loadStickers() {
        try {
            stickerPacks.value = await apiFetch('/chat/stickers')
        } catch (e) {
            console.warn('Failed to load stickers')
        }
    }

    function sendSticker(stickerId: string) {
        sendMediaMessage('sticker', stickerId)
    }

    return {
        apiFetch,
        loadConversations,
        loadMessages,
        markAsRead,
        sendMessage,
        sendMediaMessage,
        cancelEdit,
        handleReply,
        cancelReply,
        startPolling,
        stopPolling,
        startStatusPolling,
        stopStatusPolling,
        loadStickers,
        sendSticker
    }
}
