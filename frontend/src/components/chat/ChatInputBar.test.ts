import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import ChatInputBar from './ChatInputBar.vue'

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
          template: '<div class="emoji-sticker-picker-stub"></div>',
        },
      },
    },
  })
}

describe('ChatInputBar.vue', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewportMock,
    })
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
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
})