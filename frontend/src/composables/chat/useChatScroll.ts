import { ref, type Ref, nextTick } from 'vue'
import type { Message } from '../../types/chat'
import { isUnreadMessageForViewer } from '../../utils/chatUnread'
import { markMessengerPerformance } from '../../utils/messengerRefactor'
import { measureMessengerDiagnostic, recordMessengerMetric } from '../../utils/messengerDiagnosticsMetrics'

export interface UseChatScrollOptions {
    messagesContainer: Ref<HTMLElement | null>
    messages: Ref<Message[]>
    currentUserId: number
    unreadNewMessagesCount: Ref<number>
    markAsRead: () => void
    isUserAtBottom: Ref<boolean>
    showScrollButton: Ref<boolean>
}

export function useChatScroll(options: UseChatScrollOptions) {
    const {
        messagesContainer,
        messages,
        currentUserId,
        unreadNewMessagesCount,
        markAsRead,
        isUserAtBottom,
        showScrollButton
    } = options

    const isViewingReply = ref(false)
    let scrollMetricSequence = 0
    let scrollIntentVersion = 0
    const PROGRAMMATIC_SCROLL_PAGINATION_SUPPRESS_MS = 8000

    function scrollToBottom() {
        const intentVersion = ++scrollIntentVersion
        const el = messagesContainer.value
        if (el) {
            const distance = Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight)
            recordMessengerMetric('scroll-to-bottom-requested-distance', Math.round(distance), 'count', {
                unreadCount: unreadNewMessagesCount.value,
            })
        }
        markMessengerPerformance('scroll-to-bottom-requested')

        if (unreadNewMessagesCount.value > 0) {
            markAsRead()
            unreadNewMessagesCount.value = 0
        }

        setTimeout(() => {
            if (intentVersion !== scrollIntentVersion) {
                return
            }
            if (messagesContainer.value) {
                messagesContainer.value.scrollTo({
                    top: messagesContainer.value.scrollHeight,
                    behavior: 'smooth'
                })
            }
        }, 100)
    }

    function forceScrollToBottom() {
        const intentVersion = ++scrollIntentVersion
        if (unreadNewMessagesCount.value > 0) {
            markAsRead()
            unreadNewMessagesCount.value = 0
        }

        const doScroll = () => {
            if (intentVersion !== scrollIntentVersion) {
                return
            }
            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight + 1000
            }
        }

        nextTick(doScroll)
        setTimeout(doScroll, 50)
        setTimeout(doScroll, 150)
        setTimeout(doScroll, 300)
    }

    // rAF-throttled scroll handler. On weak devices the native scroll
    // event fires many times per frame; without throttling each event
    // triggers reactive writes to `isUserAtBottom`/`showScrollButton`
    // which causes layout work on every tick. Coalescing to one read
    // per animation frame keeps the scroll path jank-free while still
    // updating within ~16ms of the latest position.
    let scrollRafPending = false
    function runScrollUpdate() {
        scrollRafPending = false
        const el = messagesContainer.value
        if (!el) return

        const threshold = 100
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight
        const atBottom = distance < threshold

        if (isUserAtBottom.value !== atBottom) {
            isUserAtBottom.value = atBottom
        }
        const shouldShow = !atBottom
        if (showScrollButton.value !== shouldShow) {
            showScrollButton.value = shouldShow
        }

        if (atBottom && unreadNewMessagesCount.value > 0) {
            markAsRead()
            unreadNewMessagesCount.value = 0
        }
    }

    function handleScroll() {
        if (scrollRafPending) return
        scrollRafPending = true
        requestAnimationFrame(runScrollUpdate)
    }

    function scrollToUnreadOrBottom() {
        scrollIntentVersion += 1
        if (!messagesContainer.value) return

        const firstUnreadIndex = messages.value.findIndex(
            msg => isUnreadMessageForViewer(msg, currentUserId)
        )

        if (firstUnreadIndex >= 0) {
            const messageElements = messagesContainer.value.querySelectorAll('.message-bubble')
            if (messageElements[firstUnreadIndex]) {
                messageElements[firstUnreadIndex].scrollIntoView({ behavior: 'auto', block: 'start' })
            }
        } else {
            messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
        }
    }

    function cancelScrollIntent() {
        scrollIntentVersion += 1
    }

    const scrollToMessage = (msgId: number) => {
        const intentVersion = ++scrollIntentVersion
        const el = document.getElementById(`msg-${msgId}`) || document.getElementById(`album-item-${msgId}`)
        const container = messagesContainer.value
        const metricId = ++scrollMetricSequence
        const startMark = `scroll-to-message-${metricId}-start`
        const endMark = `scroll-to-message-${metricId}-end`
        markMessengerPerformance(startMark)

        if (el && container) {
            const safeContainer = container
            const safeEl = el

            isViewingReply.value = true

            safeEl.classList.remove('highlight-message')
            void safeEl.offsetWidth
            safeEl.classList.add('highlight-message')

            setTimeout(() => {
                isViewingReply.value = false
                safeEl.classList.remove('highlight-message')
            }, PROGRAMMATIC_SCROLL_PAGINATION_SUPPRESS_MS)

            const containerRect = safeContainer.getBoundingClientRect()
            const elRect = safeEl.getBoundingClientRect()

            const relativeTop = elRect.top - containerRect.top
            const elHeight = elRect.height
            const containerHeight = containerRect.height

            const scrollBy = relativeTop - (containerHeight / 2) + (elHeight / 2)
            const targetScrollTop = safeContainer.scrollTop + scrollBy
            recordMessengerMetric('scroll-to-message-distance', Math.round(Math.abs(scrollBy)), 'count', { msgId })

            const startScrollTop = safeContainer.scrollTop
            const distance = targetScrollTop - startScrollTop
            const duration = 1000
            const startTime = performance.now()

            function step(currentTime: number) {
                if (intentVersion !== scrollIntentVersion) {
                    return
                }

                const elapsed = currentTime - startTime
                const progress = Math.min(elapsed / duration, 1)

                const ease = 1 - Math.pow(1 - progress, 3)
                safeContainer.scrollTop = startScrollTop + (distance * ease)

                if (progress < 1) {
                    requestAnimationFrame(step)
                } else {
                    markMessengerPerformance(endMark)
                    measureMessengerDiagnostic('scroll-to-message', startMark, endMark, { msgId })
                }
            }

            requestAnimationFrame(step)
        }
    }

    return {
        isViewingReply,
        scrollToBottom,
        forceScrollToBottom,
        cancelScrollIntent,
        handleScroll,
        scrollToUnreadOrBottom,
        scrollToMessage
    }
}
