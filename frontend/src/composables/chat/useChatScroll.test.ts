import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatScroll } from './useChatScroll'

describe('useChatScroll', () => {
  let container: HTMLDivElement
  let markAsReadMock: ReturnType<typeof vi.fn>
  let originalRaf: typeof window.requestAnimationFrame

  beforeEach(() => {
    vi.useFakeTimers()
    container = document.createElement('div')
    Object.defineProperty(container, 'scrollHeight', { value: 1200, configurable: true })
    Object.defineProperty(container, 'clientHeight', { value: 400, configurable: true })
    container.scrollTop = 0
    container.scrollTo = vi.fn(({ top }: { top: number }) => {
      container.scrollTop = top
    }) as any
    markAsReadMock = vi.fn()
    originalRaf = window.requestAnimationFrame
    window.requestAnimationFrame = vi.fn((callback: FrameRequestCallback) => {
      callback(performance.now() + 1000)
      return 1
    }) as any
    document.body.innerHTML = ''
  })

  afterEach(() => {
    window.requestAnimationFrame = originalRaf
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('scrolls to bottom and clears unread counters when requested', () => {
    const unreadNewMessagesCount = ref(3)
    const isUserAtBottom = ref(false)
    const showScrollButton = ref(true)
    const subject = useChatScroll({
      messagesContainer: ref(container),
      messages: ref([]),
      currentUserId: 7,
      unreadNewMessagesCount,
      markAsRead: markAsReadMock,
      isUserAtBottom,
      showScrollButton,
    })

    subject.scrollToBottom()
    vi.advanceTimersByTime(100)

    expect(markAsReadMock).toHaveBeenCalledTimes(1)
    expect(unreadNewMessagesCount.value).toBe(0)
    expect(container.scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: 'smooth' })
  })

  it('forces repeated bottom scrolls and updates scroll state via the rAF throttled handler', async () => {
    const unreadNewMessagesCount = ref(2)
    const isUserAtBottom = ref(false)
    const showScrollButton = ref(true)
    const subject = useChatScroll({
      messagesContainer: ref(container),
      messages: ref([]),
      currentUserId: 7,
      unreadNewMessagesCount,
      markAsRead: markAsReadMock,
      isUserAtBottom,
      showScrollButton,
    })

    subject.forceScrollToBottom()
    await Promise.resolve()
    vi.advanceTimersByTime(300)

    expect(markAsReadMock).toHaveBeenCalledTimes(1)
    expect(container.scrollTop).toBe(2200)
    expect(unreadNewMessagesCount.value).toBe(0)

    container.scrollTop = 701
    subject.handleScroll()

    expect(isUserAtBottom.value).toBe(true)
    expect(showScrollButton.value).toBe(false)

    container.scrollTop = 0
    subject.handleScroll()
    expect(isUserAtBottom.value).toBe(false)
    expect(showScrollButton.value).toBe(true)
  })

  it('scrolls to the first unread bubble or falls back to the bottom when no unread exists', () => {
    const first = document.createElement('div')
    const second = document.createElement('div')
    first.className = 'message-bubble'
    second.className = 'message-bubble'
    first.scrollIntoView = vi.fn()
    second.scrollIntoView = vi.fn()
    container.append(first, second)

    const subject = useChatScroll({
      messagesContainer: ref(container),
      messages: ref([
        { id: 1, receiver_id: 8, is_read: true },
        { id: 2, receiver_id: 7, is_read: false },
      ] as any),
      currentUserId: 7,
      unreadNewMessagesCount: ref(0),
      markAsRead: markAsReadMock,
      isUserAtBottom: ref(true),
      showScrollButton: ref(false),
    })

    subject.scrollToUnreadOrBottom()
    expect(second.scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' })

    const fallbackSubject = useChatScroll({
      messagesContainer: ref(container),
      messages: ref([{ id: 3, receiver_id: 8, is_read: true }] as any),
      currentUserId: 7,
      unreadNewMessagesCount: ref(0),
      markAsRead: markAsReadMock,
      isUserAtBottom: ref(true),
      showScrollButton: ref(false),
    })

    fallbackSubject.scrollToUnreadOrBottom()
    expect(container.scrollTop).toBe(1200)
  })

  it('highlights a replied message and centers it in the scroll container', () => {
    const target = document.createElement('div')
    target.id = 'msg-50'
    document.body.appendChild(target)

    container.getBoundingClientRect = vi.fn(() => ({ top: 0, height: 400 } as DOMRect))
    target.getBoundingClientRect = vi.fn(() => ({ top: 300, height: 80 } as DOMRect))

    const subject = useChatScroll({
      messagesContainer: ref(container),
      messages: ref([]),
      currentUserId: 7,
      unreadNewMessagesCount: ref(0),
      markAsRead: markAsReadMock,
      isUserAtBottom: ref(true),
      showScrollButton: ref(false),
    })

    subject.scrollToMessage(50)

    expect(subject.isViewingReply.value).toBe(true)
    expect(target.classList.contains('highlight-message')).toBe(true)
    expect(container.scrollTop).toBe(140)

    vi.advanceTimersByTime(3000)
    expect(subject.isViewingReply.value).toBe(false)
    expect(target.classList.contains('highlight-message')).toBe(false)
  })
})