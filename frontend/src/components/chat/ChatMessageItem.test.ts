import { reactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatMessageItem from './ChatMessageItem.vue'

const chatMessageItemMocks = vi.hoisted(() => ({
  audioStore: null as {
    currentPlayingId: number | null
    setCurrentPlaying: ReturnType<typeof vi.fn>
  } | null,
  prewarmFileCacheMock: vi.fn(),
  observeVisibilityMock: vi.fn(() => () => {}),
}))

vi.mock('../../stores/audio', async () => {
  const { reactive } = await import('vue')
  const audioStore = reactive({
    currentPlayingId: null as number | null,
    setCurrentPlaying: vi.fn((messageId: number | null) => {
      audioStore.currentPlayingId = messageId
    }),
  })
  chatMessageItemMocks.audioStore = audioStore
  return {
    useAudioStore: () => audioStore,
  }
})

vi.mock('../../utils/sharedVisibilityObserver', () => ({
  observeVisibility: chatMessageItemMocks.observeVisibilityMock,
}))

vi.mock('../../utils/messageReactions', () => ({
  MESSAGE_REACTION_ORDER: new Map(),
}))

vi.mock('../../composables/chat/useChatFileHandler', async () => {
  const { reactive } = await import('vue')
  return {
    handleFileClick: vi.fn(),
    shareFile: vi.fn(),
    canShareFiles: () => false,
    useChatFileHandler: () => ({ downloadingFiles: reactive({}) }),
    prewarmFileCache: chatMessageItemMocks.prewarmFileCacheMock,
    isFileCached: vi.fn(() => false),
    seedFileCache: vi.fn(),
    useFileCacheRegistry: () => reactive({}),
  }
})

let nextPlayShouldReject = false

class FakeAudio extends EventTarget {
  src: string
  preload = ''
  duration = 8
  currentTime = 0
  readyState = 2

  constructor(src: string) {
    super()
    this.src = src
  }

  async play() {
    if (nextPlayShouldReject) {
      throw new Error('play failed')
    }
    this.dispatchEvent(new Event('loadedmetadata'))
    this.dispatchEvent(new Event('canplay'))
    this.dispatchEvent(new Event('play'))
  }

  pause() {
    this.dispatchEvent(new Event('pause'))
  }

  load() {}
}

function buildVoiceMessage(overrides: Record<string, unknown> = {}) {
  return {
    id: 51,
    sender_id: 9,
    content: JSON.stringify({ file_id: 'voice-51', durationMs: 8000 }),
    message_type: 'voice',
    created_at: '2026-05-12T10:00:00.000Z',
    is_deleted: false,
    reactions: [],
    ...overrides,
  }
}

function buildTextMessage(overrides: Record<string, unknown> = {}) {
  return {
    id: 61,
    sender_id: 9,
    content: 'forwarded text',
    message_type: 'text',
    created_at: '2026-05-12T10:00:00.000Z',
    is_deleted: false,
    reactions: [],
    ...overrides,
  }
}

function mountVoiceMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: buildVoiceMessage(messageOverrides),
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

function mountTextMessage(messageOverrides: Record<string, unknown> = {}) {
  return mount(ChatMessageItem, {
    props: {
      msg: buildTextMessage(messageOverrides),
      currentUserId: 7,
      selectedUserName: 'Ali',
      selectedMessages: [],
      imageCache: {},
      isSelectionMode: false,
    },
    global: {
      stubs: {
        ChatAlbumLayout: {
          template: '<div class="album-layout-stub"></div>',
        },
      },
    },
  })
}

describe('ChatMessageItem.vue', () => {
  const originalAudio = globalThis.Audio

  beforeEach(() => {
    chatMessageItemMocks.audioStore!.currentPlayingId = null
    chatMessageItemMocks.audioStore!.setCurrentPlaying.mockClear()
    chatMessageItemMocks.prewarmFileCacheMock.mockClear()
    chatMessageItemMocks.observeVisibilityMock.mockClear()
    nextPlayShouldReject = false
    localStorage.setItem('auth_token', 'test-token')
    vi.stubGlobal('Audio', FakeAudio)
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    localStorage.clear()
    if (originalAudio) {
      vi.stubGlobal('Audio', originalAudio)
    }
  })

  it('renders the voice waveform and toggles playback state', async () => {
    const wrapper = mountVoiceMessage()

    expect(wrapper.findAll('.voice-wave-bar')).toHaveLength(40)
    expect(chatMessageItemMocks.prewarmFileCacheMock).toHaveBeenCalledWith('voice-51')

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.voice-play-btn').classes()).toContain('is-active')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBe(51)
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(51)

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.voice-play-btn').classes()).not.toContain('is-active')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBeNull()
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(null)
  })

  it('marks the bubble as errored when voice playback fails', async () => {
    nextPlayShouldReject = true
    const wrapper = mountVoiceMessage()

    await wrapper.find('.voice-play-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.msg-voice').classes()).toContain('is-error')
    expect(chatMessageItemMocks.audioStore!.currentPlayingId).toBeNull()
    expect(chatMessageItemMocks.audioStore!.setCurrentPlaying).toHaveBeenCalledWith(null)
  })

  it('emits owner profile targets for forwarded accountant messages', async () => {
    const wrapper = mountTextMessage({
      forwarded_from_id: 12,
      forwarded_from_name: 'دفتر حسابدار',
      forwarded_from_profile_user_id: 88,
      forwarded_from_profile_account_name: 'owner-88',
      forwarded_from_highlight_accountant_user_id: 12,
      forwarded_from_highlight_accountant_relation_display_name: 'حسابدار فروش',
    })

    await wrapper.get('.forward-link').trigger('click')

    expect(wrapper.emitted('open-public-profile')).toEqual([
      [{
        id: 88,
        account_name: 'owner-88',
        highlight_accountant_user_id: 12,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      }],
    ])
  })
})