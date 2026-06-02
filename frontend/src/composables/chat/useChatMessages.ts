import { ref, watch, type Ref, nextTick } from 'vue'
import { apiFetchJson } from '../../utils/auth'
import { getUserFacingErrorMessage, type ErrorPolicyContext } from '../../utils/httpErrorPolicy'
import type { Conversation, Message } from '../../types/chat'
import { useNotificationStore } from '../../stores/notifications'
import {
    countEmojiStickerOccurrences,
    isEmojiStickerOnlyMessage,
    MAX_STICKERS_PER_MESSAGE,
} from '../../utils/emojiStickerCatalog'
import {
    buildChatMessagesEndpoint,
    buildChatReadEndpoint,
    buildChatSendBody,
    buildChatSendEndpoint,
    isChannelConversationKey,
    isRoomConversationKey,
} from '../../utils/chatRoomRouting'
import { getConversationPreviewText } from '../../utils/chatMessagePreview'
import { markMessengerPerformance } from '../../utils/messengerRefactor'
import {
    measureMessengerStage2,
    recordMessengerDomSnapshot,
    recordMessengerMetric,
    scheduleMessengerDiagnosticTask,
} from '../../utils/messengerStage2Metrics'
import {
    getPendingForUser as backgroundGetPendingForUser,
    buildOptimisticMessageFromUpload,
    waitForChatUploadBackgroundReady,
} from '../../services/chatUploadBackground'
import { formatLastSeenStatus } from '../../utils/userPresence'

const MESSENGER_CHAT_DIAGNOSTIC_DEFER_MS = 2600

export interface UseChatMessagesOptions {
    apiBaseUrl: string
    jwtToken: string | null
    currentUserId: number
    selectedUserId: Ref<number | null>
    messages: Ref<Message[]>
    conversations: Ref<Conversation[]>
    error: Ref<string>
    messagePanelError?: Ref<string>
    isLoadingMessages: Ref<boolean>
    isSending: Ref<boolean>
    unreadNewMessagesCount: Ref<number>
    isUserAtBottom: Ref<boolean>
    isViewingReply: Ref<boolean>
    targetUserStatus: Ref<string>
    selectedUserName: Ref<string>
    messageInput: Ref<string>
    editingMessage: Ref<Message | null>
    replyingToMessage: Ref<Message | null>
    swipedMessageId: Ref<number | null>
    isMobile: Ref<boolean>
    showStickerPicker: Ref<boolean>
    scrollToBottom: () => void
    scrollToUnreadOrBottom: () => void
    forceScrollToBottom: () => void
    focusMessageInput: (options?: { cursorToEnd?: boolean }) => void
    adjustTextareaHeight: () => void
    onNamedRoomUnavailable?: (conversationKey: number) => void | Promise<void>
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
        messagePanelError,
        isLoadingMessages,
        isSending,
        unreadNewMessagesCount,
        isUserAtBottom,
        isViewingReply,
        targetUserStatus,
        selectedUserName,
        messageInput,
        editingMessage,
        replyingToMessage,
        swipedMessageId,
        isMobile,
        showStickerPicker,
        scrollToBottom,
        scrollToUnreadOrBottom,
        forceScrollToBottom,
        focusMessageInput,
        adjustTextareaHeight,
        onNamedRoomUnavailable,
    } = options

    const notificationStore = useNotificationStore()
    const textSendControllers = new Map<number, AbortController>()
    const messageSnapshotCache = new Map<number, Message[]>()
    const backgroundHydrationTimers = new Map<number, number>()
    const MAX_CACHED_CHAT_SNAPSHOTS = 12
    const INITIAL_CHAT_OPEN_LIMIT = 48
    const FAST_CHAT_OPEN_LIMIT = 16
    const BACKGROUND_HYDRATION_DELAY_MS = 3200
    const SEARCH_CONTEXT_LIMIT = 50
    const OLDER_MESSAGES_PAGE_LIMIT = 60
    const hasOlderMessages = ref(true)
    const isLoadingOlderMessages = ref(false)
    const pendingBackgroundHydrationUsers = new Set<number>()
    let latestLoadRequestId = 0
    let chatLoadMetricSequence = 0
    let mutedConversationSyncSequence = 0

    function cloneMessage(message: Message): Message {
        return {
            ...message,
            reactions: Array.isArray(message.reactions)
                ? message.reactions.map(reaction => ({ ...reaction }))
                : undefined,
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

    function getConversationPreviewContent(messageType: Message['message_type'], content: string) {
        return getConversationPreviewText(messageType, content)
    }

    function upsertConversationPreview(userId: number, patch: Partial<Conversation>) {
        const existingConversation = conversations.value.find(conversation => conversation.other_user_id === userId)
        if (existingConversation) {
            Object.assign(existingConversation, patch)
            return existingConversation
        }

        if (userId <= 0) {
            return null
        }

        const newConversation: Conversation = {
            id: userId,
            other_user_id: userId,
            other_user_name: selectedUserName.value || 'گفتگوی جدید',
            last_message_content: null,
            last_message_type: null,
            last_message_at: null,
            unread_count: 0,
            room_kind: 'direct',
            ...patch,
        }

        conversations.value.unshift(newConversation)
        return newConversation
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

    function mergeServerTailIntoExistingMessages(existingMessages: Message[], latestMessages: Message[]) {
        const persistedById = new Map<number, Message>()

        for (const message of existingMessages) {
            if (message.id <= 0) continue
            persistedById.set(message.id, cloneMessage(message))
        }

        for (const message of latestMessages) {
            persistedById.set(message.id, cloneMessage(message))
        }

        return Array.from(persistedById.values()).sort((left, right) => left.id - right.id)
    }

    function isActiveLoadRequest(requestId: number, userId: number) {
        return requestId === latestLoadRequestId && selectedUserId.value === userId
    }

    function isLatestLoadRequest(requestId: number) {
        return requestId === latestLoadRequestId
    }

    function mergeLatePendingOptimisticMessages(userId: number, requestId: number) {
        void (async () => {
            await waitForChatUploadBackgroundReady()
            if (!isActiveLoadRequest(requestId, userId)) {
                return
            }
            const pendingOptimistic = getPendingOptimisticMessages(userId)
            if (pendingOptimistic.length === 0) {
                return
            }
            messages.value = mergeOptimisticMessages(messages.value, pendingOptimistic)
            storeMessageSnapshot(userId, messages.value)
        })()
    }

    function scheduleBackgroundHydration(userId: number) {
        if (pendingBackgroundHydrationUsers.has(userId)) {
            return
        }

        pendingBackgroundHydrationUsers.add(userId)
        const timerId = window.setTimeout(async () => {
            backgroundHydrationTimers.delete(userId)
            try {
                if (selectedUserId.value !== userId) {
                    return
                }
                await loadMessages(userId, true)
            } finally {
                pendingBackgroundHydrationUsers.delete(userId)
            }
        }, BACKGROUND_HYDRATION_DELAY_MS)
        backgroundHydrationTimers.set(userId, timerId)
    }

    function shouldPreferFullInitialOpen() {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
            return false
        }

        return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    }

    function cancelBackgroundHydration(userId: number) {
        const timerId = backgroundHydrationTimers.get(userId)
        if (timerId !== undefined) {
            window.clearTimeout(timerId)
            backgroundHydrationTimers.delete(userId)
        }
        pendingBackgroundHydrationUsers.delete(userId)
    }

    watch(selectedUserId, (nextUserId, previousUserId) => {
        if (typeof previousUserId === 'number' && previousUserId !== nextUserId) {
            storeMessageSnapshot(previousUserId, messages.value)
            cancelBackgroundHydration(previousUserId)
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
    let pollStartTimer: number | null = null
    const POLL_INTERVAL = 30000
    let statusPollTimer: number | null = null
    let statusPollStartTimer: number | null = null

    async function apiFetch(endpoint: string, fetchOptions: RequestInit = {}, errorContext: ErrorPolicyContext = {}) {
        return await apiFetchJson(`/api${endpoint}`, fetchOptions, errorContext)
    }

    function buildMessagesEndpoint(userId: number, query: string) {
        return buildChatMessagesEndpoint(userId, query)
    }

    function buildReadEndpoint(userId: number) {
        return buildChatReadEndpoint(userId)
    }

    function buildSendEndpoint(userId: number) {
        return buildChatSendEndpoint(userId)
    }

    async function loadConversations() {
        const hasExistingConversations = Array.isArray(conversations.value) && conversations.value.length > 0
        try {
            const loadedConversations = await apiFetch('/chat/conversations', {}, {
                surface: 'messenger',
                scope: 'list',
                operation: 'load-list',
                preserveExistingData: hasExistingConversations,
                resourceLabel: 'لیست گفتگوها',
                fallbackMessage: 'دریافت گفتگوها ممکن نشد.',
            })
            conversations.value = Array.isArray(loadedConversations) ? loadedConversations : []
            error.value = ''
            const mutedSyncId = ++mutedConversationSyncSequence
            const loadedConversationSnapshot = conversations.value
            window.setTimeout(() => {
                if (mutedSyncId !== mutedConversationSyncSequence) {
                    return
                }
                notificationStore.syncMutedConversationIds(
                    loadedConversationSnapshot
                        .filter((conversation) => conversation.is_muted)
                        .map((conversation) => conversation.other_user_id)
                )
            }, 0)
        } catch (e: any) {
            error.value = getUserFacingErrorMessage(e, {
                surface: 'messenger',
                scope: 'list',
                operation: 'load-list',
                preserveExistingData: hasExistingConversations,
                resourceLabel: 'لیست گفتگوها',
                fallbackMessage: 'دریافت گفتگوها ممکن نشد.',
            })
        }
    }

    async function loadMessages(userId: number, silent = false, aroundId?: number) {
        const requestId = ++latestLoadRequestId
        const metricId = ++chatLoadMetricSequence
        const shouldMeasureChatOpen = !silent && !aroundId
        const chatOpenStartMark = `chat-open-${metricId}-start`
        const chatOpenResponseMark = `chat-open-${metricId}-response`
        let firstMessagePaintMarked = false
        let effectiveSilent = silent
        const isChannelRoom = isChannelConversationKey(userId)

        function markFirstMessagePaint(source: 'cache' | 'server') {
            if (!shouldMeasureChatOpen || firstMessagePaintMarked) {
                return
            }

            firstMessagePaintMarked = true
            const paintMark = `chat-open-${metricId}-first-message-paint`
            markMessengerPerformance(paintMark)
            measureMessengerStage2('chat-open-to-first-message-paint', chatOpenStartMark, paintMark, {
                userId,
                source,
                messageCount: messages.value.length,
            })
            const root = typeof document !== 'undefined'
                ? document.querySelector('.messages-container') || document.body
                : null
            if (root) {
                scheduleMessengerDiagnosticTask(() => {
                    recordMessengerDomSnapshot('chat-first-message-paint', root, {
                        userId,
                        source,
                        messageCount: messages.value.length,
                    })
                }, { deferMs: MESSENGER_CHAT_DIAGNOSTIC_DEFER_MS, timeoutMs: 1200, fallbackDelayMs: 240 })
            }
        }

        if (shouldMeasureChatOpen) {
            markMessengerPerformance(chatOpenStartMark)
        }

        if (!effectiveSilent) isLoadingMessages.value = true

        try {
            const cachedSnapshot = (!aroundId && !silent) ? getMessageSnapshot(userId) : null
            const shouldUseFastOpen = !aroundId && !silent && !cachedSnapshot && !shouldPreferFullInitialOpen()
            const openLimit = shouldUseFastOpen ? FAST_CHAT_OPEN_LIMIT : INITIAL_CHAT_OPEN_LIMIT
            let shouldHydrateAfterFastOpen = shouldUseFastOpen
            let url = buildMessagesEndpoint(userId, `limit=${openLimit}&_t=${Date.now()}`)

            if (!aroundId && !silent) {
                hasOlderMessages.value = true
            }

            if (aroundId) {
                url = buildMessagesEndpoint(userId, `limit=${SEARCH_CONTEXT_LIMIT}&around_id=${aroundId}&_t=${Date.now()}`)
                if (!effectiveSilent) messages.value = []
            } else if (!effectiveSilent) {
                if (cachedSnapshot) {
                    messages.value = mergeOptimisticMessages(cachedSnapshot, getPendingOptimisticMessages(userId))
                    unreadNewMessagesCount.value = 0
                    isLoadingMessages.value = false
                    await nextTick()
                    markFirstMessagePaint('cache')
                    if (selectedUserId.value === userId) {
                        scrollToUnreadOrBottom()
                        void markAsRead()
                    }
                    // Keep refreshing from the server, but do it without
                    // showing the skeleton again.
                    effectiveSilent = true
                }
            }

            const loadedMessages = await apiFetch(url, {}, {
                surface: 'messenger',
                scope: 'panel',
                operation: aroundId ? 'load-detail' : (silent ? 'background-refresh' : 'load-detail'),
                preserveExistingData: silent || messages.value.length > 0,
                resourceLabel: 'گفتگو',
                fallbackMessage: 'دریافت پیام‌های این گفتگو ممکن نشد.',
            })
            if (shouldMeasureChatOpen) {
                markMessengerPerformance(chatOpenResponseMark)
                measureMessengerStage2('chat-open-request', chatOpenStartMark, chatOpenResponseMark, {
                    userId,
                    resultCount: Array.isArray(loadedMessages) ? loadedMessages.length : 0,
                })
            }
            if (!isActiveLoadRequest(requestId, userId)) {
                return
            }
            if (!silent) {
                if (messagePanelError) messagePanelError.value = ''
                else error.value = ''
            }

            const pendingOptimistic = effectiveSilent
                ? []
                : getPendingOptimisticMessages(userId)

            if (aroundId) {
                messages.value = loadedMessages
                hasOlderMessages.value = true
                // Don't inject pending around a reply anchor — `around_id`
                // loads a slice, not the full tail, so pending items don't
                // belong inside that slice's timeline range.
                if (isLatestLoadRequest(requestId)) {
                    isLoadingMessages.value = false
                }
                return
            }

            storeMessageSnapshot(userId, loadedMessages)
            hasOlderMessages.value = loadedMessages.length >= openLimit
            if (shouldHydrateAfterFastOpen && loadedMessages.length < FAST_CHAT_OPEN_LIMIT) {
                shouldHydrateAfterFastOpen = false
            }

            if (effectiveSilent) {
                const lastOldMsg = messages.value[messages.value.length - 1]
                const lastNewMsg = loadedMessages[loadedMessages.length - 1]
                const isNewMessage = lastNewMsg && (!lastOldMsg || lastNewMsg.id !== lastOldMsg.id)
                const oldLength = messages.value.length

                await waitForChatUploadBackgroundReady()
                const resolvedPendingOptimistic = getPendingOptimisticMessages(userId)
                const tempParams = messages.value.filter(m => m.id < 0)
                messages.value = mergeOptimisticMessages(
                    mergeServerTailIntoExistingMessages(messages.value, loadedMessages),
                    mergeOptimisticMessages(tempParams, resolvedPendingOptimistic)
                )

                if (isNewMessage) {
                    if (isChannelRoom || lastNewMsg.sender_id !== currentUserId) {
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
                markFirstMessagePaint('server')
                window.setTimeout(() => {
                    if (!isActiveLoadRequest(requestId, userId)) {
                        return
                    }
                    scrollToUnreadOrBottom()
                    void markAsRead()
                }, 0)

                // Don't block first paint waiting for upload service restore.
                // Reconcile resumed pending uploads after initial render.
                mergeLatePendingOptimisticMessages(userId, requestId)

                if (shouldHydrateAfterFastOpen && selectedUserId.value === userId) {
                    scheduleBackgroundHydration(userId)
                }
            }
        } catch (e: any) {
            if (isRoomConversationKey(userId) && selectedUserId.value === userId && (e?.status === 403 || e?.status === 404)) {
                await onNamedRoomUnavailable?.(userId)
            }
            if (!effectiveSilent && isActiveLoadRequest(requestId, userId)) {
                const message = getUserFacingErrorMessage(e, {
                    surface: 'messenger',
                    scope: 'panel',
                    operation: aroundId ? 'load-detail' : 'load-detail',
                    preserveExistingData: messages.value.length > 0,
                    resourceLabel: 'گفتگو',
                    fallbackMessage: 'دریافت پیام‌های این گفتگو ممکن نشد.',
                })
                if (messagePanelError) messagePanelError.value = message
                else error.value = message
            }
            if (!effectiveSilent && isLatestLoadRequest(requestId)) isLoadingMessages.value = false
            if (shouldMeasureChatOpen) {
                recordMessengerMetric('chat-open-error', 1, 'count', {
                    userId,
                    status: typeof e?.status === 'number' ? e.status : null,
                })
            }
        } finally {
            if (!effectiveSilent && isLatestLoadRequest(requestId) && isLoadingMessages.value) {
                isLoadingMessages.value = false
            }
        }
    }

    async function loadOlderMessages(userId: number) {
        if (isLoadingMessages.value || isLoadingOlderMessages.value || !hasOlderMessages.value) {
            return 0
        }

        const oldestLoadedMessage = messages.value.find(message => message.id > 0)
        if (!oldestLoadedMessage) {
            hasOlderMessages.value = false
            return 0
        }

        isLoadingOlderMessages.value = true

        try {
            const olderMessages = await apiFetch(
                buildMessagesEndpoint(userId, `limit=${OLDER_MESSAGES_PAGE_LIMIT}&before_id=${oldestLoadedMessage.id}&_t=${Date.now()}`)
            )

            if (selectedUserId.value !== userId) {
                return 0
            }

            if (!Array.isArray(olderMessages) || olderMessages.length === 0) {
                hasOlderMessages.value = false
                return 0
            }

            const loadedIds = new Set(messages.value.map(message => message.id))
            const uniqueOlderMessages = olderMessages.filter(message => !loadedIds.has(message.id))

            if (uniqueOlderMessages.length === 0) {
                hasOlderMessages.value = olderMessages.length >= OLDER_MESSAGES_PAGE_LIMIT
                return 0
            }

            messages.value = [...uniqueOlderMessages, ...messages.value]
            storeMessageSnapshot(userId, messages.value)
            hasOlderMessages.value = olderMessages.length >= OLDER_MESSAGES_PAGE_LIMIT
            return uniqueOlderMessages.length
        } finally {
            isLoadingOlderMessages.value = false
        }
    }

    async function markAsRead() {
        if (!selectedUserId.value) return
        try {
            await apiFetch(buildReadEndpoint(selectedUserId.value), { method: 'POST' })
            const conv = conversations.value.find(c => c.other_user_id === selectedUserId.value)
            if (conv) {
                conv.unread_count = 0
            }
            notificationStore.markChatAsRead(selectedUserId.value)
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
            const body = buildChatSendBody(selectedUserId.value, {
                content,
                message_type: type,
            })

            const newMsg = await apiFetch(buildSendEndpoint(selectedUserId.value), {
                method: 'POST',
                body: JSON.stringify(body)
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

            upsertConversationPreview(selectedUserId.value, {
                last_message_at: hydratedMsg.created_at,
                last_message_type: hydratedMsg.message_type,
                last_message_content: getConversationPreviewContent(hydratedMsg.message_type, hydratedMsg.content || ''),
                unread_count: 0,
            })
            void loadConversations()

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
        const isChannelRoom = isChannelConversationKey(selectedUserId.value)
        const content = messageInput.value;
        const stickerCount = countEmojiStickerOccurrences(content)
        if (stickerCount > MAX_STICKERS_PER_MESSAGE) {
            alert(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)
            return
        }

        const messageType: Message['message_type'] = isEmojiStickerOnlyMessage(content) ? 'sticker' : 'text'
        const replyTo = replyingToMessage.value;

        const tempId = -Date.now();
        const tempMsg: Message = {
            id: tempId,
            sender_id: currentUserId,
            receiver_id: isChannelRoom ? currentUserId : selectedUserId.value,
            content: content,
            message_type: messageType,
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
            const body = buildChatSendBody(selectedUserId.value, {
                content,
                message_type: messageType,
                reply_to_message_id: replyTo?.id,
            });

            const abortController = new AbortController();
            textSendControllers.set(tempId, abortController);

            const serverMsg = await apiFetch(buildSendEndpoint(selectedUserId.value), {
                method: 'POST',
                body: JSON.stringify(body),
                signal: abortController.signal
            });

            textSendControllers.delete(tempId);

            const idx = messages.value.findIndex(m => m.id === tempId);
            if (idx !== -1) {
                messages.value[idx] = serverMsg;
            }

            upsertConversationPreview(selectedUserId.value, {
                last_message_at: serverMsg.created_at,
                last_message_type: serverMsg.message_type,
                last_message_content: getConversationPreviewContent(serverMsg.message_type, serverMsg.content || ''),
                unread_count: 0,
            })
            void loadConversations()

            nextTick(() => {
                if (!showStickerPicker.value) {
                    focusMessageInput()
                }
            });
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
            focusMessageInput()
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

            targetUserStatus.value = formatLastSeenStatus(userData.last_seen_at, {
                emptyText: 'آخرین بازدید خیلی وقت پیش',
            }) || 'آخرین بازدید خیلی وقت پیش'
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

    function startPolling(options: { initialDelayMs?: number } = {}) {
        stopPolling()
        const initialDelayMs = Number(options.initialDelayMs ?? 0)
        if (initialDelayMs > 0) {
            pollStartTimer = window.setTimeout(() => {
                pollStartTimer = null
                pollTimer = window.setInterval(poll, POLL_INTERVAL)
            }, initialDelayMs)
            return
        }

        pollTimer = window.setInterval(poll, POLL_INTERVAL)
    }

    function stopPolling() {
        if (pollStartTimer) {
            clearTimeout(pollStartTimer)
            pollStartTimer = null
        }
        if (pollTimer) {
            clearInterval(pollTimer)
            pollTimer = null
        }
    }

    function startStatusPolling(userId: number, options: { initialDelayMs?: number } = {}) {
        stopStatusPolling()
        const initialDelayMs = Number(options.initialDelayMs ?? 0)
        const start = () => {
            void fetchTargetUserStatus(userId)
            statusPollTimer = window.setInterval(() => fetchTargetUserStatus(userId), 30000)
        }

        if (initialDelayMs > 0) {
            statusPollStartTimer = window.setTimeout(() => {
                statusPollStartTimer = null
                start()
            }, initialDelayMs)
            return
        }

        start()
    }

    function stopStatusPolling() {
        if (statusPollStartTimer) {
            clearTimeout(statusPollStartTimer)
            statusPollStartTimer = null
        }
        if (statusPollTimer) {
            clearInterval(statusPollTimer)
            statusPollTimer = null
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
        loadOlderMessages,
        markAsRead,
        sendMessage,
        sendMediaMessage,
        hasOlderMessages,
        isLoadingOlderMessages,
        cancelEdit,
        handleReply,
        cancelReply,
        startPolling,
        stopPolling,
        startStatusPolling,
        stopStatusPolling,
        sendSticker
    }
}
