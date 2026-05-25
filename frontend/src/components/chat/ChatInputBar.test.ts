import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import ChatInputBar from './ChatInputBar.vue'
import { MAX_STICKERS_PER_MESSAGE } from '../../utils/emojiStickerCatalog'

const chatInputBarMocks = vi.hoisted(() => ({
  recorderStart: vi.fn(async () => {}),
  recorderStop: vi.fn(async () => new Blob(['voice'], { type: 'audio/webm' })),
  recorderCancel: vi.fn(),
  lastTick: null as null | ((ms: number) => void),
}))

vi.mock('../../utils/audioRecorder', () => ({
  AudioRecorder: class {
    constructor(onTick: (ms: number) => void) {
      chatInputBarMocks.lastTick = onTick
    }

    start() {
      return chatInputBarMocks.recorderStart()
    }

    stop() {
      return chatInputBarMocks.recorderStop()
    }

    cancel() {
      chatInputBarMocks.recorderCancel()
    }
  },
}))

let resizeObserverCallback: ResizeObserverCallback | null = null

class ResizeObserverMock {
  constructor(callback: ResizeObserverCallback) {
    resizeObserverCallback = callback
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

const visualViewportMock = {
  height: 900,
  width: 400,
  offsetTop: 0,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
}

function mountInputBar(overrides: Record<string, unknown> = {}) {
  return mount(ChatInputBar, {
    props: {
      modelValue: '',
      editingMessage: null,
      replyingToMessage: null,
      currentUserId: 7,
      selectedUserName: 'Ali',
      isSelectionMode: true,
      selectedMessages: [101],
      canDeleteSelected: true,
      canCopySelected: true,
      isUploading: false,
      isSending: false,
      ...overrides,
    },
    global: {
      directives: {
        ripple: {},
      },
      stubs: {
        EmojiStickerPicker: {
          name: 'EmojiStickerPicker',
          props: ['open', 'currentUserId', 'currentStickerCount', 'maxStickerCount', 'closeOnSelect', 'panelHeight', 'disableTransition'],
          template: `
            <div class="emoji-sticker-picker-stub">
              <button class="insert-sticker-btn" @click="$emit('insert', '🔥')">insert</button>
              <button class="backspace-sticker-btn" @click="$emit('backspace')">backspace</button>
            </div>
          `,
        },
      },
    },
  })
}

function getInputBarTestHooks(wrapper: ReturnType<typeof mountInputBar>) {
  const exposed = wrapper.vm.$.exposed as { __testHooks?: any } | null
  if (!exposed?.__testHooks) {
    throw new Error('ChatInputBar test hooks are unavailable in the current harness')
  }
  return exposed.__testHooks
}

describe('ChatInputBar.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    chatInputBarMocks.recorderStart.mockReset()
    chatInputBarMocks.recorderStart.mockResolvedValue(undefined)
    chatInputBarMocks.recorderStop.mockReset()
    chatInputBarMocks.recorderStop.mockResolvedValue(new Blob(['voice'], { type: 'audio/webm' }))
    chatInputBarMocks.recorderCancel.mockReset()
    chatInputBarMocks.lastTick = null
    resizeObserverCallback = null
    visualViewportMock.height = 900
    visualViewportMock.width = 400
    visualViewportMock.offsetTop = 0
    visualViewportMock.addEventListener.mockReset()
    visualViewportMock.removeEventListener.mockReset()
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewportMock,
    })
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
    delete (window as typeof window & { __chatInputDebug?: unknown }).__chatInputDebug
  })

  it('renders the single-selection action bar and emits every supported action', async () => {
    const wrapper = mountInputBar()

    expect(wrapper.find('.selection-bottom-bar').exists()).toBe(true)
    expect(wrapper.find('.voice-btn').exists()).toBe(false)
    expect(wrapper.find('.attach-btn').exists()).toBe(false)

    const deleteButton = wrapper.findAll('.selection-action-btn').find((item) => item.text().includes('حذف'))
    const replyButton = wrapper.findAll('.selection-action-btn').find((item) => item.text().includes('پاسخ'))
    const copyButton = wrapper.findAll('.selection-action-btn').find((item) => item.text().includes('کپی'))
    const forwardButton = wrapper.findAll('.selection-action-btn').find((item) => item.text().includes('هدایت'))

    expect(deleteButton).toBeTruthy()
    expect(replyButton).toBeTruthy()
    expect(copyButton).toBeTruthy()
    expect(forwardButton).toBeTruthy()

    await deleteButton!.trigger('click')
    await replyButton!.trigger('click')
    await copyButton!.trigger('click')
    await forwardButton!.trigger('click')

    expect(wrapper.emitted('delete-selected')).toHaveLength(1)
    expect(wrapper.emitted('reply-selected')).toHaveLength(1)
    expect(wrapper.emitted('copy-selected')).toHaveLength(1)
    expect(wrapper.emitted('forward-selected')).toHaveLength(1)
  })

  it('hides single-message actions when multiple messages are selected', () => {
    const wrapper = mountInputBar({
      selectedMessages: [101, 202],
      canDeleteSelected: false,
      canCopySelected: false,
    })

    expect(wrapper.find('.selection-bottom-bar').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('حذف')
    expect(wrapper.text()).not.toContain('پاسخ')
    expect(wrapper.text()).not.toContain('کپی')
    expect(wrapper.text()).toContain('هدایت')
  })

  it('keeps the attachment button available when the composer has text', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      canDeleteSelected: false,
      canCopySelected: false,
      modelValue: 'کپشن تست',
    })

    expect(wrapper.find('.attach-btn').exists()).toBe(true)
    expect(wrapper.find('.voice-btn').exists()).toBe(false)
    expect(wrapper.find('.send-btn-inline').exists()).toBe(true)

    await wrapper.find('.attach-btn').trigger('click')

    expect(wrapper.emitted('toggle-attachment')).toHaveLength(1)
  })

  it('syncs the live textarea value before opening attachments', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      canDeleteSelected: false,
      canCopySelected: false,
      modelValue: '',
    })
    const hooks = getInputBarTestHooks(wrapper)
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    textarea.value = 'کپشن تازه'
    hooks.handleToggleAttachment()
    await nextTick()

    expect(wrapper.emitted('update:modelValue')).toContainEqual(['کپشن تازه'])
    expect(wrapper.emitted('toggle-attachment')).toEqual([['کپشن تازه']])
  })

  it('emits cancel events for reply/edit banners and sends trimmed text on enter', async () => {
    const replyingWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      replyingToMessage: {
        id: 88,
        sender_id: 12,
        content: 'پاسخ قدیمی',
        message_type: 'text',
      },
    })

    expect(replyingWrapper.find('.reply-banner').exists()).toBe(true)
    await replyingWrapper.find('.close-reply').trigger('click')
    expect(replyingWrapper.emitted('cancel-reply')).toHaveLength(1)

    const editingWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      editingMessage: {
        id: 99,
        sender_id: 7,
        content: 'متن قبلی',
        message_type: 'text',
      },
      modelValue: '  متن ویرایش شده  ',
    })

    expect(editingWrapper.find('.edit-banner').exists()).toBe(true)
    await editingWrapper.find('.close-reply').trigger('click')
    expect(editingWrapper.emitted('cancel-edit')).toHaveLength(1)

    await editingWrapper.find('textarea').trigger('keydown.enter', {
      preventDefault: vi.fn(),
      shiftKey: false,
    })

    expect(editingWrapper.emitted('send-text')).toEqual([['متن ویرایش شده']])
    expect(editingWrapper.emitted('update:modelValue')).toContainEqual([''])
  })

  it('inserts stickers from the picker and deletes them with backspace', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '',
    })

    await wrapper.find('.insert-sticker-btn').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toContainEqual(['🔥'])
    expect(wrapper.emitted('typing')).toHaveLength(1)

    await wrapper.setProps({ modelValue: '🔥' })
    await wrapper.find('.backspace-sticker-btn').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toContainEqual([''])
  })

  it('records voice messages, emits send-voice for long recordings, and can cancel recording locally', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '',
    })
    const vm = wrapper.vm as unknown as {
      startVoiceRecording: () => Promise<void>
      stopVoiceRecording: () => Promise<void>
      cancelVoiceRecording: () => void
    }

    await vm.startVoiceRecording()
    chatInputBarMocks.lastTick?.(1200)
    await flushPromises()

    expect(chatInputBarMocks.recorderStart).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.recording-state').exists()).toBe(true)

    await vm.stopVoiceRecording()
    await flushPromises()

    expect(chatInputBarMocks.recorderStop).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('send-voice')).toHaveLength(1)
    expect(wrapper.emitted('send-voice')?.[0]?.[1]).toBe(1200)

    const cancelWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '',
    })
    const cancelVm = cancelWrapper.vm as unknown as {
      startVoiceRecording: () => Promise<void>
      cancelVoiceRecording: () => void
    }

    await cancelVm.startVoiceRecording()
    chatInputBarMocks.lastTick?.(1400)
    await flushPromises()
    cancelVm.cancelVoiceRecording()

    expect(chatInputBarMocks.recorderCancel).toHaveBeenCalledTimes(1)
  })

  it('alerts when starting voice recording fails', async () => {
    chatInputBarMocks.recorderStart.mockRejectedValueOnce(new Error('no mic'))
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '',
    })
    const vm = wrapper.vm as unknown as {
      startVoiceRecording: () => Promise<void>
    }

    await vm.startVoiceRecording()
    await flushPromises()

    expect(window.alert).toHaveBeenCalledWith('امکان دسترسی به میکروفون وجود ندارد.')
    expect(wrapper.find('.recording-state').exists()).toBe(false)
  })

  it('renders deleted and read-only disabled banners instead of the composer', () => {
    const deletedWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      isDeleted: true,
    })

    expect(deletedWrapper.find('.disabled-banner').exists()).toBe(true)
    expect(deletedWrapper.text()).toContain('امکان ارسال پیام به این کاربر وجود ندارد.')
    expect(deletedWrapper.find('textarea').exists()).toBe(false)

    const readOnlyWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      isReadOnly: true,
      readOnlyBannerText: 'ارسال فقط برای مدیران مجاز است.',
    })

    expect(readOnlyWrapper.find('.disabled-banner').exists()).toBe(true)
    expect(readOnlyWrapper.text()).toContain('ارسال فقط برای مدیران مجاز است.')
    expect(readOnlyWrapper.find('textarea').exists()).toBe(false)
  })

  it('toggles the sticker picker and closes it before opening attachments or returning focus to the keyboard', async () => {
    const pickerWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: false,
    })

    await pickerWrapper.get('.emoji-btn').trigger('click')
    expect(pickerWrapper.emitted('update:stickerPickerOpen')).toContainEqual([true])

    const attachmentWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    await attachmentWrapper.get('.attach-btn').trigger('click')
    expect(attachmentWrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
    expect(attachmentWrapper.emitted('toggle-attachment')).toHaveLength(1)

    const focusWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    await focusWrapper.get('textarea').trigger('focus')
    expect(focusWrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
  })

  it('does not emit send-voice for recordings shorter than the minimum threshold', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '',
    })
    const vm = wrapper.vm as unknown as {
      startVoiceRecording: () => Promise<void>
      stopVoiceRecording: () => Promise<void>
    }

    await vm.startVoiceRecording()
    chatInputBarMocks.lastTick?.(300)
    await flushPromises()

    await vm.stopVoiceRecording()
    await flushPromises()

    expect(chatInputBarMocks.recorderStop).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('send-voice')).toBeUndefined()
    expect(wrapper.find('.recording-state').exists()).toBe(false)
  })

  it('blocks inserts and sends when the sticker count exceeds the message cap', async () => {
    const maxedMessage = new Array(MAX_STICKERS_PER_MESSAGE).fill('🔥').join('')
    const insertionWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: maxedMessage,
    })

    await insertionWrapper.find('.insert-sticker-btn').trigger('click')
    expect(window.alert).toHaveBeenCalledWith(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)
    expect(insertionWrapper.emitted('update:modelValue')).toBeUndefined()

    const overLimitMessage = new Array(MAX_STICKERS_PER_MESSAGE + 1).fill('🔥').join('')
    const sendWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: overLimitMessage,
    })

    await sendWrapper.get('.send-btn-inline').trigger('click')
    expect(window.alert).toHaveBeenCalledWith(`حداکثر ${MAX_STICKERS_PER_MESSAGE} استیکر در هر پیام مجاز است.`)
    expect(sendWrapper.emitted('send-text')).toBeUndefined()
  })

  it('summarizes reply and edit banners for non-text message types', () => {
    const replyWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      replyingToMessage: {
        id: 41,
        sender_id: 12,
        content: '{}',
        message_type: 'document',
      },
    })

    expect(replyWrapper.find('.reply-banner-text').text()).toContain('📎 فایل')

    const editWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      editingMessage: {
        id: 42,
        sender_id: 7,
        content: '{}',
        message_type: 'location',
      },
      modelValue: 'ویرایش موقعیت',
    })

    expect(editWrapper.find('.reply-banner-text').text()).toContain('📍 موقعیت')
  })

  it('keeps the emoji toggle inert for deleted chats and exposes focus helpers for the composer', async () => {
    const deletedWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      isDeleted: true,
      stickerPickerOpen: false,
    })

    const deletedVm = deletedWrapper.vm as unknown as {
      focusInput: (options?: { cursorToEnd?: boolean }) => void
      adjustTextareaHeight: () => void
    }

    deletedVm.focusInput({ cursorToEnd: true })
    await nextTick()
    expect(deletedWrapper.emitted('update:stickerPickerOpen')).toBeUndefined()

    const activeWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'متن تست',
    })
    const activeVm = activeWrapper.vm as unknown as {
      focusInput: (options?: { cursorToEnd?: boolean }) => void
      adjustTextareaHeight: () => void
    }

    const textarea = activeWrapper.get('textarea').element as HTMLTextAreaElement
    const focusSpy = vi.spyOn(textarea, 'focus')
    const setSelectionRangeSpy = vi.spyOn(textarea, 'setSelectionRange')
    textarea.style.height = '10px'
    Object.defineProperty(textarea, 'scrollHeight', {
      configurable: true,
      get: () => 240,
    })

    activeVm.adjustTextareaHeight()
    expect(textarea.style.height).toBe('200px')

    activeVm.focusInput({ cursorToEnd: true })
    await nextTick()
    expect(focusSpy).toHaveBeenCalled()
    expect(setSelectionRangeSpy).toHaveBeenCalledWith(textarea.value.length, textarea.value.length)
  })

  it('covers picker sizing fallbacks, debug window sync, and open/close transient picker helpers', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })
    const hooks = getInputBarTestHooks(wrapper)
    const debugWindow = window as typeof window & {
      __chatInputDebug?: { getSnapshot: () => Record<string, unknown>; getTrail: () => string[] }
    }

    hooks.state.keyboardInsetEnvSupported.value = false
    hooks.state.keyboardHeight.value = 144
    hooks.state.lastKnownKeyboardHeight.value = 188
    await nextTick()

    const picker = wrapper.findComponent({ name: 'EmojiStickerPicker' })
    expect(Number(picker.props('panelHeight'))).toBeGreaterThan(0)

    await wrapper.setProps({ stickerPickerOpen: false })
    hooks.state.lockedComposerInsetHeight.value = 260
    hooks.state.pendingPickerOpenAfterKeyboardClose.value = true
    await nextTick()

    const spacer = wrapper.get('.picker-transition-spacer')
    expect(spacer.attributes('style')).toContain('height: 116px')

    hooks.state.isChatDebugEnabled.value = true
    hooks.captureDebugState('manual-debug')
    hooks.syncDebugWindowHandle()
    expect(debugWindow.__chatInputDebug?.getSnapshot().lastEvent).toBe('manual-debug')
    expect(debugWindow.__chatInputDebug?.getTrail()[0]).toContain('manual-debug')

    hooks.state.pendingPickerOpenAfterKeyboardClose.value = true
    hooks.state.pendingKeyboardReturn.value = true
    hooks.state.disablePickerTransition.value = true
    hooks.openStickerPickerAfterKeyboardClose()
    await nextTick()
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([true])
    expect(hooks.state.disablePickerTransition.value).toBe(false)

    await wrapper.setProps({ stickerPickerOpen: true })
    hooks.state.pendingKeyboardReturn.value = true
    hooks.state.lockedComposerInsetHeight.value = 144
    hooks.state.disablePickerTransition.value = true
    hooks.finalizeKeyboardReturn()
    expect(hooks.state.pendingKeyboardReturn.value).toBe(false)
    expect(hooks.state.lockedComposerInsetHeight.value).toBe(0)
    expect(hooks.state.disablePickerTransition.value).toBe(false)
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])

    hooks.state.isChatDebugEnabled.value = false
    hooks.syncDebugWindowHandle()
    expect(debugWindow.__chatInputDebug).toBeUndefined()
  })

  it('covers selection capture, attachment toggles, textarea focus swaps, and restored composer selections', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'hello world',
      stickerPickerOpen: true,
    })
    const hooks = getInputBarTestHooks(wrapper)
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement
    const inputContainer = wrapper.get('.input-container').element as HTMLElement
    const blurSpy = vi.spyOn(textarea, 'blur').mockImplementation(() => {})
    const setSelectionRangeSpy = vi.spyOn(textarea, 'setSelectionRange')
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })

    Object.defineProperty(inputContainer, 'scrollHeight', { configurable: true, value: 300 })
    Object.defineProperty(inputContainer, 'clientHeight', { configurable: true, value: 120 })
    Object.defineProperty(inputContainer, 'scrollWidth', { configurable: true, value: 200 })
    Object.defineProperty(inputContainer, 'clientWidth', { configurable: true, value: 100 })
    inputContainer.scrollTop = 44
    inputContainer.scrollLeft = 6

    textarea.focus()
    textarea.setSelectionRange(2, 5)
    hooks.captureSelection()
    expect(hooks.getComposerSelection()).toEqual({ start: 2, end: 5 })
    setSelectionRangeSpy.mockClear()

    hooks.state.keyboardHeight.value = 180
    hooks.prepareStickerToggle()
    expect(hooks.state.lockedComposerInsetHeight.value).toBe(180)

    hooks.handleToggleAttachment()
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
    expect(wrapper.emitted('toggle-attachment')).toHaveLength(1)

    hooks.prepareTextareaFocus()
    hooks.handleTextareaFocus()
    await nextTick()
    expect(blurSpy).not.toHaveBeenCalled()
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])

    Object.defineProperty(document, 'activeElement', {
      configurable: true,
      get: () => textarea,
    })
    hooks.applyComposerValue('hi', 99, 99)
    await nextTick()
    expect(setSelectionRangeSpy).toHaveBeenLastCalledWith(11, 11)

    hooks.blurInput()
    expect(blurSpy).toHaveBeenCalled()
  })

  it('switches from the keyboard to the sticker picker and closes the picker again when focusing the textarea', async () => {
    vi.useFakeTimers()
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'باز کردن پنل',
      stickerPickerOpen: false,
    })

    const textarea = wrapper.get('textarea')
    await textarea.trigger('focus')
    await flushPromises()

    await wrapper.get('.emoji-btn').trigger('mousedown')
    await wrapper.get('.emoji-btn').trigger('click')
    await vi.advanceTimersByTimeAsync(320)
    await flushPromises()

    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([true])

    await wrapper.setProps({ stickerPickerOpen: true })
    await flushPromises()

    await textarea.trigger('focus')
    await flushPromises()

    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
  })

  it('preserves composer scroll position across textarea focus and emits typing on input', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'draft',
    })
    const textarea = wrapper.get('textarea')
    const inputContainer = wrapper.get('.input-container').element as HTMLElement
    const originalGetComputedStyle = window.getComputedStyle

    Object.defineProperty(window, 'scrollX', { configurable: true, value: 14 })
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 28 })
    Object.defineProperty(inputContainer, 'scrollHeight', { configurable: true, value: 600 })
    Object.defineProperty(inputContainer, 'clientHeight', { configurable: true, value: 200 })
    Object.defineProperty(inputContainer, 'scrollWidth', { configurable: true, value: 500 })
    Object.defineProperty(inputContainer, 'clientWidth', { configurable: true, value: 150 })
    inputContainer.scrollTop = 77
    inputContainer.scrollLeft = 9

    vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element) => {
      if (element === inputContainer) {
        return { overflowY: 'auto', overflowX: 'scroll' } as CSSStyleDeclaration
      }
      return originalGetComputedStyle(element)
    })
    const scrollToSpy = vi.mocked(window.scrollTo)
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })

    await textarea.trigger('mousedown')
    inputContainer.scrollTop = 0
    inputContainer.scrollLeft = 0
    await textarea.trigger('focus')

    expect(inputContainer.scrollTop).toBe(77)
    expect(inputContainer.scrollLeft).toBe(9)
    expect(scrollToSpy).toHaveBeenCalledWith(14, 28)

    await textarea.setValue('updated draft')

    expect(wrapper.emitted('typing')).toBeTruthy()
    expect(wrapper.emitted('update:modelValue')).toContainEqual(['updated draft'])
  })

  it('uses the keyboard env probe for picker sizing and finalizes picker-to-keyboard return on viewport resize', async () => {
    vi.stubGlobal('CSS', {
      supports: vi.fn(() => true),
    })
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    expect(resizeObserverCallback).toBeTruthy()
    resizeObserverCallback?.([
      { contentRect: { height: 288 } as DOMRectReadOnly } as ResizeObserverEntry,
    ], {} as ResizeObserver)
    await nextTick()

    const picker = wrapper.findComponent({ name: 'EmojiStickerPicker' })
    expect(picker.props('panelHeight')).toBe('max(0px, calc(288px - env(keyboard-inset-height, 0px)))')

    await wrapper.get('.emoji-btn').trigger('click')
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
    await wrapper.setProps({ stickerPickerOpen: false })

    const resizeHandler = visualViewportMock.addEventListener.mock.calls.find(([eventName]) => eventName === 'resize')?.[1]
    expect(resizeHandler).toBeTruthy()
    visualViewportMock.height = 620
    ;(resizeHandler as EventListener)(new Event('resize'))
    await nextTick()

    expect(picker.props('open')).toBe(false)
  })

  it('clears pending picker state when editing or selection mode starts', async () => {
    const editingWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    await editingWrapper.setProps({
      editingMessage: {
        id: 201,
        sender_id: 7,
        content: 'old text',
        message_type: 'text',
      },
    })

    expect(editingWrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])

    const selectionWrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    await selectionWrapper.setProps({
      isSelectionMode: true,
      selectedMessages: [7],
    })

    expect(selectionWrapper.emitted('update:stickerPickerOpen')).toContainEqual([false])
  })

  it('supports selected-range and grapheme backspace while ignoring shift-enter sends', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'hello world',
      stickerPickerOpen: true,
    })
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    textarea.focus()
    textarea.setSelectionRange(0, 5)
    await wrapper.get('textarea').trigger('select')
    await wrapper.find('.backspace-sticker-btn').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toContainEqual([' world'])

    await wrapper.setProps({ modelValue: 'A🔥' })
    await nextTick()
    textarea.focus()
    textarea.setSelectionRange(textarea.value.length, textarea.value.length)
    await wrapper.get('textarea').trigger('keyup')
    await wrapper.find('.backspace-sticker-btn').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toContainEqual(['A'])

    await wrapper.get('textarea').trigger('keydown.enter', {
      preventDefault: vi.fn(),
      shiftKey: true,
    })
    expect(wrapper.emitted('send-text')).toBeUndefined()
  })

  it('falls back to plain focus and disables rich controls when requested', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'focus me',
      disableRichComposer: true,
      allowVoiceRecording: false,
    })
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement
    const vm = wrapper.vm as unknown as {
      focusInput: (options?: { cursorToEnd?: boolean }) => void
    }
    let focusCalls = 0
    vi.spyOn(textarea, 'focus').mockImplementation(((options?: FocusOptions) => {
      focusCalls += 1
      if (options?.preventScroll) {
        throw new Error('preventScroll unsupported')
      }
    }) as HTMLTextAreaElement['focus'])
    const setSelectionRangeSpy = vi.spyOn(textarea, 'setSelectionRange')

    expect(wrapper.find('.attach-btn').exists()).toBe(false)
    expect(wrapper.find('.voice-btn').exists()).toBe(false)

    vm.focusInput({ cursorToEnd: true })
    await nextTick()

    expect(focusCalls).toBe(2)
    expect(setSelectionRangeSpy).toHaveBeenCalledWith(textarea.value.length, textarea.value.length)
  })

  it('uses fallback picker sizing when env support is unavailable and cleans viewport listeners on unmount', async () => {
    vi.stubGlobal('CSS', {
      supports: vi.fn(() => false),
    })
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    const picker = wrapper.findComponent({ name: 'EmojiStickerPicker' })
    expect(picker.props('panelHeight')).toBe(336)

    wrapper.unmount()
    expect(visualViewportMock.removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(visualViewportMock.removeEventListener).toHaveBeenCalledWith('scroll', expect.any(Function))
  })

  it('falls back to window.innerHeight when visualViewport is unavailable', async () => {
    vi.stubGlobal('CSS', {
      supports: vi.fn(() => false),
    })
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: undefined,
    })
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: 640,
    })

    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    expect(wrapper.findComponent({ name: 'EmojiStickerPicker' }).props('panelHeight')).toBe(269)
  })

  it('handles throwing CSS support probes and env-probe entries without content', async () => {
    vi.stubGlobal('CSS', {
      supports: vi.fn(() => {
        throw new Error('css probe failed')
      }),
    })
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      stickerPickerOpen: true,
    })

    const picker = wrapper.findComponent({ name: 'EmojiStickerPicker' })
  expect(picker.props('panelHeight')).toBe(336)

    resizeObserverCallback?.([], {} as ResizeObserver)
    await nextTick()
  expect(picker.props('panelHeight')).toBe(336)

    resizeObserverCallback?.([
      { contentRect: { height: 180 } as DOMRectReadOnly } as ResizeObserverEntry,
    ], {} as ResizeObserver)
    await nextTick()
    expect(wrapper.findComponent({ name: 'EmojiStickerPicker' }).props('panelHeight')).toBe('max(0px, calc(180px - env(keyboard-inset-height, 0px)))')
  })

  it('covers selection clamp, active selection reads, env keyboard sizing, and direct picker open', async () => {
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'abc',
    })
    const hooks = getInputBarTestHooks(wrapper)
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    hooks.state.messageInputRef.value = null
    hooks.state.composerSelectionStart.value = 9
    hooks.state.composerSelectionEnd.value = 11
    hooks.captureSelection()
    expect(hooks.state.composerSelectionStart.value).toBe(3)
    expect(hooks.state.composerSelectionEnd.value).toBe(3)

    hooks.state.messageInputRef.value = textarea
    textarea.focus()
    textarea.setSelectionRange(1, 2)
    Object.defineProperty(document, 'activeElement', {
      configurable: true,
      get: () => textarea,
    })
    expect(hooks.getComposerSelection()).toEqual({ start: 1, end: 2 })

    hooks.state.keyboardInsetEnvSupported.value = true
    hooks.state.envKeyboardInset.value = 144
    hooks.state.envKeyboardInsetMax.value = 188
    expect(hooks.getMeasuredKeyboardInset()).toBe(188)

    Object.defineProperty(document, 'activeElement', {
      configurable: true,
      get: () => document.body,
    })
    hooks.state.keyboardHeight.value = 0
    hooks.state.lastKnownKeyboardHeight.value = 0
    await wrapper.get('.emoji-btn').trigger('click')
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([true])
  })

  it('covers keyboard-open picker transition timers and viewport metric branches', async () => {
    vi.useFakeTimers()
    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: 'keyboard flow',
      stickerPickerOpen: false,
    })
    const hooks = getInputBarTestHooks(wrapper)
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement
    const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')

    hooks.state.viewportBaseHeight.value = 0
    hooks.state.lockedComposerInsetHeight.value = 33
    hooks.state.pendingKeyboardReturn.value = false
    visualViewportMock.height = 640
    hooks.updateKeyboardMetrics()
    expect(hooks.state.viewportBaseHeight.value).toBeGreaterThan(0)
    expect(hooks.state.lockedComposerInsetHeight.value).toBe(0)

    hooks.state.keyboardHeight.value = 180
    textarea.focus()
    textarea.setSelectionRange(2, 4)
    hooks.prepareStickerToggle()
    expect(hooks.state.lockedComposerInsetHeight.value).toBe(180)

    await wrapper.get('.emoji-btn').trigger('mousedown')
    await wrapper.get('.emoji-btn').trigger('click')
    expect(hooks.state.pendingPickerOpenAfterKeyboardClose.value).toBe(true)
    expect(hooks.state.disablePickerTransition.value).toBe(true)

    visualViewportMock.height = 900
    hooks.state.viewportBaseHeight.value = 900
    await vi.advanceTimersByTimeAsync(320)
    await nextTick()

    expect(clearTimeoutSpy).toHaveBeenCalled()
    expect(wrapper.emitted('update:stickerPickerOpen')).toContainEqual([true])
  })

  it('summarizes remaining media types and keeps empty send/voice guards inert', async () => {
    for (const [messageType, expected] of [
      ['image', 'تصویر'],
      ['video', 'ویدیو'],
      ['voice', 'پیام صوتی'],
      ['sticker', 'استیکر'],
    ] as const) {
      const wrapper = mountInputBar({
        isSelectionMode: false,
        selectedMessages: [],
        replyingToMessage: {
          id: 77,
          sender_id: 12,
          content: '{}',
          message_type: messageType,
        },
      })

      expect(wrapper.find('.reply-banner-text').text()).toContain(expected)
      wrapper.unmount()
    }

    const wrapper = mountInputBar({
      isSelectionMode: false,
      selectedMessages: [],
      modelValue: '   ',
    })
    const vm = wrapper.vm as unknown as {
      stopVoiceRecording: () => Promise<void>
      cancelVoiceRecording: () => void
    }

    await wrapper.get('textarea').trigger('keydown.enter', {
      preventDefault: vi.fn(),
      shiftKey: false,
    })
    expect(wrapper.emitted('send-text')).toBeUndefined()

    await vm.stopVoiceRecording()
    vm.cancelVoiceRecording()
    expect(chatInputBarMocks.recorderStop).not.toHaveBeenCalled()
    expect(chatInputBarMocks.recorderCancel).not.toHaveBeenCalled()
  })
})