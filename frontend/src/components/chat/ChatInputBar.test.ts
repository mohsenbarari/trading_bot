import { beforeEach, describe, expect, it, vi } from 'vitest'
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

class ResizeObserverMock {
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

describe('ChatInputBar.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    chatInputBarMocks.recorderStart.mockReset()
    chatInputBarMocks.recorderStart.mockResolvedValue(undefined)
    chatInputBarMocks.recorderStop.mockReset()
    chatInputBarMocks.recorderStop.mockResolvedValue(new Blob(['voice'], { type: 'audio/webm' }))
    chatInputBarMocks.recorderCancel.mockReset()
    chatInputBarMocks.lastTick = null
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewportMock,
    })
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    vi.spyOn(window, 'alert').mockImplementation(() => {})
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
})