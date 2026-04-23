import { watch, type Ref, nextTick } from 'vue'
import { apiFetchJson } from '../../utils/auth'
import type { Conversation, Message, StickerPack } from '../../types/chat'
import { useNotificationStore } from '../../stores/notifications'
import {
    getPendingForUser as backgroundGetPendingForUser,
    buildOptimisticMessageFromUpload,
} from '../../services/chatUploadBackground'

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
    const textSendControllers = new Map<number, AbortController>()
    const messageSnapshotCache = new Map<number, Message[]>()
    const MAX_CACHED_CHAT_SNAPSHOTS = 12
    let latestLoadRequestId = 0

    function cloneMessage(message: Message): Message {
        return {
            ...message,
            reply_to_message: message.reply_to_message
                ? { ...message.reply_to_message }
                : undefined
        }
    }

    function trimMessageSnapshot(messagesList: Message[]) {
        return messagesList
            .filter(message => message.id > 0)
            .map(cloneMessage)
    }

    function storeMessageSnapshot(userId: number, messagesList: Message[]) {
        const snapshot = trimMessageSnapshot(messagesList)

        if (messageSnapshotCache.has(userId)) {
            messageSnapshotCache.delete(userId)
        }

        messageSnapshotCache.set(userId, snapshot)

        if (messageSnapshotCache.size > MAX_CACHED_CHAT_SNAPSHOTS) {
            const oldestKey = messageSnapshotCache.keys().next().value
            if (typeof oldestKey === 'number') {
                messageSnapshotCache.delete(oldestKey)
            }
        }
    }

    function getMessageSnapshot(userId: number) {
        const snapshot = messageSnapshotCache.get(userId)
        if (snapshot === undefined || snapshot.length === 0) {
            return null
        }

        // Refresh insertion order so the map acts like a tiny LRU cache.
        messageSnapshotCache.delete(userId)
        messageSnapshotCache.set(userId, snapshot)
        return snapshot.map(cloneMessage)
    }

    function getPendingOptimisticMessages(userId: number) {
        return backgroundGetPendingForUser(userId).map(buildOptimisticMessageFromUpload)
    }

    function mergeOptimisticMessages(baseMessages: Message[], optimisticMessages: Message[]) {
        if (optimisticMessages.length === 0) {
            return baseMessages
        }

        const merged = [...baseMessages]
        const seen = new Set<number>(baseMessages.map(message => message.id))

        for (const message of optimisticMessages) {
            if (seen.has(message.id)) continue
            seen.add(message.id)
            merged.push(cloneMessage(message))
        }

        return merged
    }

    function isActiveLoadRequest(requestId: number, userId: number) {
        return requestId === latestLoadRequestId && selectedUserId.value === userId
    }

    function isLatestLoadRequest(requestId: number) {
        return requestId === latestLoadRequestId
    }

    watch(selectedUserId, (nextUserId, previousUserId) => {
        if (typeof previousUserId === 'number' && previousUserId !== nextUserId) {
            storeMessageSnapshot(previousUserId, messages.value)
        }
    })

    function cancelTextMessage(id: number) {
        const controller = textSendControllers.get(id);
        if (controller) {
            controller.abort();
            textSendControllers.delete(id);
        }
        const index = messages.value.findIndex(m => m.id === id);
        if (index !== -1) {
            messages.value.splice(index, 1);
        }
    }

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
        const requestId = ++latestLoadRequestId
        let effectiveSilent = silent

        if (!effectiveSilent) isLoadingMessages.value = true

        try {
            let url = `/chat/messages/${userId}?limit=200&_t=${Date.now()}`

            if (aroundId) {
                url = `/chat/messages/${userId}?limit=50&around_id=${aroundId}&_t=${Date.now()}`
                if (!effectiveSilent) messages.value = []
            } else if (!effectiveSilent) {
                const cachedMessages = getMessageSnapshot(userId)
                if (cachedMessages) {
                    messages.value = mergeOptimisticMessages(cachedMessages, getPendingOptimisticMessages(userId))
                    unreadNewMessagesCount.value = 0
                    isLoadingMessages.value = false
                    await nextTick()
                    if (selectedUserId.value === userId) {
                        scrollToUnreadOrBottom()
                        void markAsRead()
                    }
                    // Keep refreshing from the server, but do it without
                    // showing the skeleton again.
                    effectiveSilent = true
                }
            }

            const loadedMessages = await apiFetch(url)
            if (!isActiveLoadRequest(requestId, userId)) {
                return
            }

            // Append any pending background-service uploads for this user
            // (both currently uploading + those resumed from IndexedDB after
            // a page reload) so the optimistic messages are visible on mount.
            const pendingOptimistic = getPendingOptimisticMessages(userId)

            if (aroundId) {
                messages.value = loadedMessages
                // Don't inject pending around a reply anchor — `around_id`
                // loads a slice, not the full tail, so pending items don't
                // belong inside that slice's timeline range.
                if (isLatestLoadRequest(requestId)) {
                    isLoadingMessages.value = false
                }
                return
            }

            storeMessageSnapshot(userId, loadedMessages)

            if (effectiveSilent) {
                const lastOldMsg = messages.value[messages.value.length - 1]
                const lastNewMsg = loadedMessages[loadedMessages.length - 1]
                const isNewMessage = lastNewMsg && (!lastOldMsg || lastNewMsg.id !== lastOldMsg.id)
                const oldLength = messages.value.length

                const tempParams = messages.value.filter(m => m.id < 0)
                messages.value = mergeOptimisticMessages(
                    loadedMessages,
                    mergeOptimisticMessages(tempParams, pendingOptimistic)
                )

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
                messages.value = mergeOptimisticMessages(loadedMessages, pendingOptimistic)
                unreadNewMessagesCount.value = 0
                if (isLatestLoadRequest(requestId)) {
                    isLoadingMessages.value = false
                }
                await nextTick()
                scrollToUnreadOrBottom()
                void markAsRead()
            }
        } catch (e: any) {
            if (!effectiveSilent && isActiveLoadRequest(requestId, userId)) error.value = e.message
            if (!effectiveSilent && isLatestLoadRequest(requestId)) isLoadingMessages.value = false
        } finally {
            if (!effectiveSilent && isLatestLoadRequest(requestId) && isLoadingMessages.value) {
                isLoadingMessages.value = false
            }
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

    async function sendMediaMessage(
        type: 'image' | 'video' | 'voice' | 'sticker',
        content: string,
        localBlobUrl?: string,
        optimisticId?: number
    ) {
        if (!selectedUserId.value) return null

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
            const hydratedMsg = localBlobUrl
                ? { ...newMsg, local_blob_url: localBlobUrl }
                : newMsg

            if (typeof optimisticId === 'number') {
                const optimisticIndex = messages.value.findIndex(m => m.id === optimisticId)
                if (optimisticIndex !== -1) {
                    messages.value[optimisticIndex] = hydratedMsg
                } else {
                    messages.value.push(hydratedMsg)
                }
            } else {
                messages.value.push(hydratedMsg)
            }

            showStickerPicker.value = false
            scrollToBottom()
            return hydratedMsg
        } catch (e: any) {
            alert(e?.message || 'خطا در ارسال رسانه')
            return null
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

            const abortController = new AbortController();
            textSendControllers.set(tempId, abortController);

            const serverMsg = await apiFetch('/chat/send', {
                method: 'POST',
                body: JSON.stringify(body),
                signal: abortController.signal
            });

            textSendControllers.delete(tempId);

            const idx = messages.value.findIndex(m => m.id === tempId);
            if (idx !== -1) {
                messages.value[idx] = serverMsg;
            }

            nextTick(() => messageInputRef.value?.focus());
        } catch (err: any) {
            textSendControllers.delete(tempId);
            if (err.name === 'AbortError') return;

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
        cancelTextMessage,
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
